"""Check and install portable application updates from GitHub Releases."""

import hashlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests


RELEASE_API_URL = (
    "https://api.github.com/repos/Bode512/Aules_PDF_Synchronizer/releases/latest"
)
EXECUTABLE_NAME = "Aules_PDF_Synchronizer.exe"
REQUEST_TIMEOUT = 30


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    download_url: str
    expected_size: int
    sha256: Optional[str]


def _version_tuple(version: str) -> Tuple[int, ...]:
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", version.strip())
    if not match:
        raise ValueError(f"Versión de release no válida: {version}")
    parts = tuple(int(part) for part in match.group(1).split("."))
    while len(parts) > 1 and parts[-1] == 0:
        parts = parts[:-1]
    return parts


def check_for_update(current_version: str) -> Optional[ReleaseInfo]:
    response = requests.get(
        RELEASE_API_URL,
        headers={"Accept": "application/vnd.github+json"},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    release = response.json()
    latest_version = release.get("tag_name", "")
    latest_parts = _version_tuple(latest_version)
    current_parts = _version_tuple(current_version)
    component_count = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (component_count - len(latest_parts))
    current_parts += (0,) * (component_count - len(current_parts))
    if latest_parts <= current_parts:
        return None

    assets = release.get("assets", [])
    asset = next(
        (item for item in assets if item.get("name") == EXECUTABLE_NAME),
        None,
    )
    if asset is None:
        raise RuntimeError(
            f"La release {latest_version} no contiene {EXECUTABLE_NAME}."
        )
    download_url = asset["browser_download_url"]
    parsed_url = urlparse(download_url)
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname != "github.com"
        or not parsed_url.path.startswith(
            "/Bode512/Aules_PDF_Synchronizer/releases/download/"
        )
    ):
        raise RuntimeError("La URL del ejecutable de actualización no es válida.")

    digest = asset.get("digest") or ""
    sha256 = digest.partition(":")[2] if digest.startswith("sha256:") else None
    return ReleaseInfo(
        version=latest_version.lstrip("v"),
        download_url=download_url,
        expected_size=int(asset.get("size", 0)),
        sha256=sha256,
    )


def download_update(release: ReleaseInfo, target: Path) -> Path:
    """Download and validate the new executable beside the current one."""
    target = target.resolve()
    temporary_path = None
    try:
        with requests.get(
            release.download_url,
            stream=True,
            timeout=REQUEST_TIMEOUT,
        ) as response:
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".update",
                dir=str(target.parent),
                delete=False,
            ) as output:
                temporary_path = Path(output.name)
                digest = hashlib.sha256()
                size = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)

        if size < 2 or (release.expected_size and size != release.expected_size):
            raise RuntimeError("El tamaño del ejecutable descargado no es válido.")
        if release.sha256 and digest.hexdigest().casefold() != release.sha256.casefold():
            raise RuntimeError("La verificación SHA-256 del ejecutable ha fallado.")
        with temporary_path.open("rb") as executable:
            if executable.read(2) != b"MZ":
                raise RuntimeError("El archivo descargado no es un ejecutable de Windows.")
        return temporary_path
    except Exception:
        if temporary_path and temporary_path.exists():
            temporary_path.unlink()
        raise


def schedule_update(staged_executable: Path, target: Path, process_id: int) -> None:
    """Replace the running portable EXE after it exits, without elevation."""
    target = target.resolve()
    staged_executable = staged_executable.resolve()
    script = r"""param([int]$ProcessId, [string]$StagedPath, [string]$TargetPath)
$ErrorActionPreference = 'Stop'
try {
    Wait-Process -Id $ProcessId -ErrorAction SilentlyContinue
    Move-Item -LiteralPath $StagedPath -Destination $TargetPath -Force
    Start-Process -FilePath $TargetPath
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show(
        "No se pudo instalar la actualización. Comprueba que la carpeta permite escritura sin permisos de administrador.`n`n$($_.Exception.Message)",
        "Aules PDF Synchronizer"
    ) | Out-Null
} finally {
    Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
}
"""
    script_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            suffix=".ps1",
            delete=False,
        ) as script_file:
            script_file.write(script)
            script_path = Path(script_file.name)
        subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(process_id),
                str(staged_executable),
                str(target),
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
    except Exception:
        if script_path and script_path.exists():
            script_path.unlink()
        if staged_executable.exists():
            staged_executable.unlink()
        raise


def current_executable_path() -> Optional[Path]:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve()
    return None
