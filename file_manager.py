"""
Gestión de archivos y carpetas: sanitización, creación de estructura y detección de colisiones.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from config import MAX_FILENAME_LENGTH, NO_SECTION_FOLDER


# Caracteres no válidos en Windows + macOS + Linux + caracteres de control
INVALID_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Nombres reservados de Windows (case-insensitive)
RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """
    Sanitiza un nombre de archivo o carpeta para que sea válido en Windows, macOS y Linux.
    - Elimina/sustituye caracteres inválidos.
    - Elimina espacios y puntos problemáticos al final.
    - Evita nombres reservados de Windows.
    - Limita longitud.
    - Evita nombres vacíos.
    """
    if not name or not str(name).strip():
        return "sin_nombre"

    name = str(name).strip()

    # Sustituir caracteres inválidos
    name = INVALID_CHARS_RE.sub(replacement, name)

    # Colapsar múltiples espacios/reemplazos
    name = re.sub(rf"{re.escape(replacement)}+", replacement, name)
    name = re.sub(r"\s+", " ", name).strip()

    # Quitar puntos y espacios finales (problemáticos en Windows)
    name = name.rstrip(" .")

    # Evitar nombres reservados
    base, ext = os.path.splitext(name)
    if base.upper() in RESERVED_NAMES:
        base = f"_{base}"
    name = base + ext

    # Limitar longitud (dejamos margen para posibles sufijos)
    if len(name) > MAX_FILENAME_LENGTH:
        base, ext = os.path.splitext(name)
        max_base = MAX_FILENAME_LENGTH - len(ext) - 5
        name = base[:max_base].rstrip(" .") + ext

    if not name or name in (".", ".."):
        return "sin_nombre"

    return name


def ensure_unique_path(path: Path) -> Path:
    """
    Si la ruta ya existe, añade un sufijo numérico seguro (_1, _2, ...).
    """
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def build_destination_path(
    base_dir: Path,
    course_name: str,
    section_name: Optional[str],
    filename: str,
    unique: bool = True,
) -> Path:
    """
    Construye la ruta final siguiendo la estructura:
    base_dir / Asignatura / Tema o "Sin sección" / archivo
    """
    course_safe = sanitize_filename(course_name)
    section_safe = sanitize_filename(section_name) if section_name else NO_SECTION_FOLDER
    file_safe = sanitize_filename(filename)

    dest_dir = base_dir / course_safe / section_safe
    dest_dir.mkdir(parents=True, exist_ok=True)

    final_path = dest_dir / file_safe
    return ensure_unique_path(final_path) if unique else final_path


def create_temp_file(suffix: str = ".pdf", directory: Optional[Path] = None) -> Path:
    """Crea un archivo temporal y devuelve su Path."""
    fd, path = tempfile.mkstemp(suffix=suffix, dir=str(directory) if directory else None)
    os.close(fd)
    return Path(path)


def move_temp_to_final(temp_path: Path, final_path: Path) -> None:
    """Mueve el archivo temporal a su ubicación definitiva de forma atómica cuando es posible."""
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(temp_path), str(final_path))


def cleanup_temp(temp_path: Optional[Path]) -> None:
    """Elimina un archivo temporal si existe."""
    if temp_path and temp_path.exists():
        try:
            temp_path.unlink()
        except OSError:
            pass