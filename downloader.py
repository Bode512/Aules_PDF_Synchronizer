"""
Descargas HTTP fiables con sesión autenticada, validación de PDF y reintentos.
"""

import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, Callable
from urllib.parse import urljoin, urlparse

import requests

from config import (
    MAX_RETRIES,
    RETRY_BACKOFF_BASE,
    REQUEST_TIMEOUT,
    CHUNK_SIZE,
    USER_AGENT,
)
from file_manager import create_temp_file, move_temp_to_final, cleanup_temp

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
            "Accept": "application/pdf,application/octet-stream,*/*",
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

    def is_pdf_content(self, response: requests.Response) -> bool:
        """Comprueba de forma robusta si la respuesta es realmente un PDF."""
        content_type = response.headers.get("Content-Type", "").lower()
        if "application/pdf" in content_type:
            return True
        # Algunos servidores no envían Content-Type correcto; miramos la firma mágica
        content = response.content[:8] if response.content else b""
        return content.startswith(b"%PDF")

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
        - Descarga a temporal y mueve solo si es PDF válido.
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
                    if "text/html" in content_type and "pdf" not in content_type:
                        # Probablemente página de login o error
                        return DownloadResult(
                            success=False,
                            error="Respuesta HTML en lugar de PDF (posible sesión caducada o enlace de visualización).",
                        )

                    temp_path = create_temp_file(".pdf")
                    size = 0
                    with open(temp_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                            if cancel_check and cancel_check():
                                cleanup_temp(temp_path)
                                return DownloadResult(success=False, error="Cancelado por el usuario")
                            if chunk:
                                f.write(chunk)
                                size += len(chunk)

                    # Validar que es PDF
                    with open(temp_path, "rb") as f:
                        header = f.read(8)
                    if not header.startswith(b"%PDF"):
                        cleanup_temp(temp_path)
                        return DownloadResult(
                            success=False,
                            error="El contenido descargado no es un PDF válido.",
                        )

                    move_temp_to_final(temp_path, dest_path)
                    return DownloadResult(success=True, path=dest_path, size=size)

            except Exception as e:
                last_error = str(e)
                cleanup_temp(temp_path)
                logger.warning("Error en intento %s de descarga de %s: %s", attempt, url, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** attempt)

        return DownloadResult(success=False, error=last_error or "Error desconocido")