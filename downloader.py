"""
Descargas HTTP fiables con sesión autenticada, validación de PDF y reintentos.
"""

import logging
import mimetypes
import time
from email.message import Message
from pathlib import Path
from typing import Optional, Callable
from urllib.parse import unquote, urljoin, urlparse

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
    move_temp_to_final,
    cleanup_temp,
    sanitize_filename,
)

logger = logging.getLogger(__name__)


class DownloadResult:
    def __init__(
        self,
        success: bool,
        path: Optional[Path] = None,
        skipped: bool = False,
        error: Optional[str] = None,
        size: int = 0,
    ):
        self.success = success
        self.path = path
        self.skipped = skipped
        self.error = error
        self.size = size


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
    def _response_filename(response: requests.Response, fallback: str) -> str:
        content_disposition = response.headers.get("Content-Disposition", "")
        if content_disposition:
            disposition = Message()
            disposition["content-disposition"] = content_disposition
            filename = disposition.get_filename()
            if filename:
                filename = unquote(filename)
                if Path(filename).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
                    return sanitize_filename(Path(filename).name)

        url_name = unquote(Path(urlparse(response.url).path).name)
        if Path(url_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
            return sanitize_filename(url_name)

        fallback_name = sanitize_filename(fallback)
        if Path(fallback_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS:
            return fallback_name

        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        extension = mimetypes.guess_extension(content_type) if content_type else None
        if content_type == "application/rtf":
            extension = ".rtf"
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

    def download(
        self,
        url: str,
        dest_path: Path,
        cancel_check: Optional[Callable[[], bool]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> DownloadResult:
        """
        Descarga un recurso a dest_path.
        - Si el archivo ya existe, se omite.
        - Descarga a temporal y valida la firma según la extensión.
        - Reintentos con backoff.
        """
        if dest_path.exists() and dest_path.stat().st_size > 0:
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
                        return DownloadResult(
                            success=False,
                            error=f"Acceso denegado (HTTP {resp.status_code}). Sesión posiblemente caducada.",
                        )
                    if resp.status_code >= 400:
                        last_error = f"HTTP {resp.status_code}"
                        raise requests.HTTPError(last_error)

                    # Comprobar Content-Type antes de guardar todo
                    content_type = resp.headers.get("Content-Type", "").lower()
                    if "text/html" in content_type:
                        return DownloadResult(
                            success=False,
                            error="Respuesta HTML en lugar de un archivo (posible sesión caducada o enlace de visualización).",
                        )

                    filename = self._response_filename(resp, dest_path.name)
                    extension = Path(filename).suffix.lower()
                    if extension not in SUPPORTED_FILE_EXTENSIONS:
                        return DownloadResult(
                            success=False,
                            error=f"Formato no compatible: {extension or 'sin extensión'}.",
                        )

                    requested_path = dest_path.parent / filename
                    if requested_path.exists() and requested_path.stat().st_size > 0:
                        return DownloadResult(success=True, path=requested_path, skipped=True)
                    final_path = ensure_unique_path(requested_path)

                    temp_path = create_temp_file(extension)
                    size = 0
                    with open(temp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                            if cancel_check and cancel_check():
                                cleanup_temp(temp_path)
                                return DownloadResult(success=False, error="Cancelado por el usuario")
                            if chunk:
                                f.write(chunk)
                                size += len(chunk)

                    if not self._is_valid_file(temp_path, extension):
                        cleanup_temp(temp_path)
                        return DownloadResult(
                            success=False,
                            error=f"El contenido descargado no es un archivo {extension} válido.",
                        )

                    move_temp_to_final(temp_path, final_path)
                    return DownloadResult(success=True, path=final_path, size=size)

            except Exception as e:
                last_error = str(e)
                cleanup_temp(temp_path)
                logger.warning("Error en intento %s de descarga de %s: %s", attempt, url, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** attempt)

        return DownloadResult(success=False, error=last_error or "Error desconocido")