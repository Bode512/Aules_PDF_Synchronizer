"""
Descargas HTTP fiables con sesión autenticada, validación de PDF y reintentos.
"""

import logging
import mimetypes
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Callable
from urllib.parse import unquote, urljoin, urlparse, parse_qs

import requests

from config import (
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    REQUEST_TIMEOUT,
    CHUNK_SIZE,
    SUPPORTED_FILE_EXTENSIONS,
    USER_AGENT,
)
from file_manager import (
    create_temp_file,
    ensure_unique_path,
    cleanup_temp,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


class _MoodleFileLinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        for attribute in ("href", "src", "data"):
            value = attributes.get(attribute)
            if value:
                self.urls.append(value)


class DownloadResult:
    def __init__(
        self,
        success: bool,
        path: Optional[Path] = None,
        skipped: bool = False,
        error: Optional[str] = None,
        size: int = 0,
        replaced: bool = False,
    ):
        self.success = success
        self.path = path
        self.skipped = skipped
        self.error = error
        self.size = size
        self.replaced = replaced


class AuthenticatedDownloader:
    """
    Gestiona una sesión requests autenticada a partir de cookies de Selenium.
    """

    def __init__(self, cookies: list, base_url: str):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream,*/*",
            "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        })
        self.base_url = base_url.rstrip("/") + "/"

        # Transferir cookies de Selenium
        for cookie in cookies:
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

    @staticmethod
    def _decode_filename(raw: str) -> str:
        """
        Decodifica un nombre de archivo de Moodle (Content-Disposition o URL):
        - deshace el %20 / %C3%A1 (percent-encoding UTF-8),
        - corrige el mojibake de doble codificación ("Ã¡" -> "á", "AnÃ¡lisis" -> "Análisis").
        """
        name = unquote(raw.strip().strip('"'))
        if not name:
            return name
        # Moodle a veces envía UTF-8 doblemente codificado: los acentos llegan como Ã¡, Ã©, Ã­...
        if re.search(r"[ÃÂ]", name):
            for encoding in ("cp1252", "latin-1"):
                try:
                    fixed = name.encode(encoding).decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    continue
                if fixed and not re.search(r"[ÃÂ]", fixed):
                    name = fixed
                    break
        return name

    @classmethod
    def _content_disposition_filename(cls, content_disposition: str) -> Optional[str]:
        """
        Extrae el nombre del header Content-Disposition soportando:
        filename="..." (con %20), filename*=UTF-8''... (RFC 5987) y variantes de Moodle.
        """
        # RFC 5987: filename*=UTF-8''Tema%201.pdf (prioridad)
        match = re.search(
            r"filename\*\s*=\s*(?P<charset>[^']*)''(?P<value>[^;]+)",
            content_disposition,
            re.IGNORECASE,
        )
        if match:
            charset = (match.group("charset").strip() or "utf-8").lower()
            return cls._decode_filename(
                unquote(match.group("value"), encoding=charset, errors="replace")
            )

        match = re.search(r'filename\s*=\s*"?([^";]+)"?', content_disposition, re.IGNORECASE)
        if match:
            return cls._decode_filename(match.group(1))
        return None

    @classmethod
    def _response_filename(cls, response: requests.Response, fallback: str) -> str:
        content_disposition = response.headers.get("Content-Disposition", "")
        if content_disposition:
            filename = cls._content_disposition_filename(content_disposition)
            if filename:
                if Path(filename).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
                    return sanitize_filename(Path(filename).name)

        url_name = cls._decode_filename(Path(urlparse(response.url).path).name)
        if Path(url_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
            return sanitize_filename(url_name)

        fallback_name = sanitize_filename(fallback)
        if Path(fallback_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
            return fallback_name

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(content_type) if content_type else None
        if content_type == "application/rtf":
            extension = ".rtf"
        if content_type == "image/jpeg":
            extension = ".jpg"
        if content_type == "image/png":
            extension = ".png"
        if content_type == "image/gif":
            extension = ".gif"
        if content_type == "image/webp":
            extension = ".webp"
        if extension in SUPPORTED_FILE_EXTENSIONS:
            return sanitize_filename(f"{fallback_name}.{extension.lstrip('.')}")
        return fallback_name

    @staticmethod
    def _is_valid_file(path: Path, extension: str) -> bool:
        with open(path, "rb") as file:
            content = file.read(4096)
        signature = content[:8]
        if content.lstrip().lower().startswith((b"<!doctype html", b"<html")):
            return False

        if extension == ".pdf":
            return signature.startswith(b"%PDF")
        if extension in {".jpg", ".jpeg"}:
            return signature.startswith(b"\xff\xd8\xff")
        if extension == ".png":
            return signature.startswith(b"\x89PNG\r\n\x1a\n")
        if extension == ".gif":
            return signature.startswith(b"GIF8")
        if extension == ".webp":
            return signature.startswith(b"RIFF") and content[8:12] == b"WEBP"
        if extension == ".bmp":
            return signature.startswith(b"BM")
        if extension == ".svg":
            return content.lstrip()[:1] == b"<" or content[:16].startswith(b"<?xml")
        if extension in {".doc", ".ppt", ".xls"}:
            return signature.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
        if extension in {".docx", ".pptx", ".xlsx", ".odt", ".odp", ".ods"}:
            return signature.startswith(b"PK")
        if extension == ".rtf":
            return signature.startswith(b"{\\rtf")
        if extension in {".txt", ".csv"}:
            try:
                content.decode("utf-8-sig")
                return True
            except UnicodeDecodeError:
                return False
        return False

    @staticmethod
    def _find_file_url(html: str, page_url: str) -> Optional[str]:
        parser = _MoodleFileLinkParser()
        parser.feed(html)
        page_host = urlparse(page_url).hostname

        for value in parser.urls:
            candidate = urljoin(page_url, value)
            parsed = urlparse(candidate)
            if parsed.hostname != page_host:
                continue
            extension = Path(unquote(parsed.path)).suffix.lower()
            if extension not in SUPPORTED_FILE_EXTENSIONS:
                query_filename = parse_qs(parsed.query).get("filename", [""])[0]
                extension = Path(unquote(query_filename)).suffix.lower()
            if extension in SUPPORTED_FILE_EXTENSIONS:
                return candidate
        return None

    def _save_response(
        self,
        response: requests.Response,
        dest_path: Path,
        cancel_check: Optional[Callable[[], bool]],
        on_progress: Optional[Callable[[str], None]],
        overwrite_existing: bool,
    ) -> DownloadResult:
        if response.status_code in (401, 403):
            if on_progress:
                on_progress(
                    f"Acceso denegado (HTTP {response.status_code}); no se reintenta. "
                    "Comprueba la sesión o los permisos del recurso."
                )
            return DownloadResult(
                success=False,
                error=f"Acceso denegado (HTTP {response.status_code}). Sesión posiblemente caducada.",
            )
        if response.status_code >= 400:
            raise requests.HTTPError(f"HTTP {response.status_code}")

        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in content_type:
            return DownloadResult(
                success=False,
                error="La URL del recurso devuelve HTML en lugar del archivo.",
            )

        filename = self._response_filename(response, dest_path.name)
        extension = Path(filename).suffix.lower()
        if extension not in SUPPORTED_FILE_EXTENSIONS:
            return DownloadResult(
                success=False,
                error=f"Formato no compatible: {extension or 'sin extensión'}.",
            )

        requested_path = dest_path.parent / filename
        if (
            requested_path.exists()
            and requested_path.stat().st_size > 0
            and not overwrite_existing
        ):
            return DownloadResult(success=True, path=requested_path, skipped=True)
        replaced = overwrite_existing and requested_path.exists()
        final_path = requested_path if overwrite_existing else ensure_unique_path(requested_path)

        temp_path = create_temp_file(extension, directory=final_path.parent)
        try:
            size = 0
            with open(temp_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if cancel_check and cancel_check():
                        return DownloadResult(success=False, error="Cancelado por el usuario")
                    if chunk:
                        file.write(chunk)
                        size += len(chunk)

            if not self._is_valid_file(temp_path, extension):
                return DownloadResult(
                    success=False,
                    error=f"El contenido descargado no es un archivo {extension} válido.",
                )

            os.replace(temp_path, final_path)
            return DownloadResult(
                success=True,
                path=final_path,
                size=size,
                replaced=replaced,
            )
        finally:
            cleanup_temp(temp_path)

    def download(
        self,
        url: str,
        dest_path: Path,
        cancel_check: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
        overwrite_existing: bool = False,
    ) -> DownloadResult:
        """
        Descarga un recurso a dest_path.
        - Si el archivo ya existe, se omite salvo que se solicite actualizarlo.
        - Descarga a temporal y valida la firma según la extensión.
        - Reintentos con backoff.
        """
        if (
            dest_path.exists()
            and dest_path.stat().st_size > 0
            and not overwrite_existing
        ):
            return DownloadResult(success=True, path=dest_path, skipped=True)

        if cancel_check and cancel_check():
            return DownloadResult(success=False, error="Cancelado por el usuario")

        # Resolver URL absoluta
        if not url.startswith("http"):
            url = urljoin(self.base_url, url)

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            if cancel_check and cancel_check():
                return DownloadResult(success=False, error="Cancelado por el usuario")

            temp_path = None
            try:
                if on_progress:
                    on_progress(f"Intento {attempt}/{MAX_RETRIES}: {url}")

                with self.session.get(
                    url,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                ) as resp:
                    if resp.status_code in (401, 403):
                        return self._save_response(
                            resp,
                            dest_path,
                            cancel_check,
                            on_progress,
                            overwrite_existing,
                        )
                    if resp.status_code >= 400:
                        raise requests.HTTPError(f"HTTP {resp.status_code}")

                    content_type = resp.headers.get("Content-Type", "").lower()
                    if "text/html" in content_type:
                        file_url = self._find_file_url(resp.text, resp.url)
                        if not file_url:
                            return DownloadResult(
                                success=False,
                                error="No se encontró un enlace al archivo dentro del recurso de Moodle.",
                            )
                        if on_progress:
                            on_progress("Recurso de Moodle localizado; descargando el archivo original...")
                    else:
                        return self._save_response(
                            resp,
                            dest_path,
                            cancel_check,
                            on_progress,
                            overwrite_existing,
                        )

                with self.session.get(
                    file_url,
                    stream=True,
                    timeout=REQUEST_TIMEOUT,
                    allow_redirects=True,
                ) as file_response:
                    return self._save_response(
                        file_response,
                        dest_path,
                        cancel_check,
                        on_progress,
                        overwrite_existing,
                    )

            except Exception as e:
                last_error = str(e)
                logger.warning("Error en intento %s de descarga de %s: %s", attempt, url, e)
                if attempt < MAX_RETRIES:
                    wait_seconds = int(RETRY_BACKOFF_BASE ** attempt)
                    for remaining in range(wait_seconds, 0, -1):
                        if cancel_check and cancel_check():
                            return DownloadResult(success=False, error="Cancelado por el usuario")
                        if on_progress:
                            on_progress(
                                f"Esperando {remaining} s antes de reintentar "
                                f"({attempt + 1}/{MAX_RETRIES}). Motivo: {last_error}"
                            )
                        time.sleep(1)

        return DownloadResult(success=False, error=last_error or "Error desconocido")