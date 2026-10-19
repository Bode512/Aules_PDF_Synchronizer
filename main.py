#!/usr/bin/env python3
"""
Punto de entrada de Aules PDF Synchronizer.
"""

import logging
import sys
from pathlib import Path

# Asegurar que el directorio del proyecto está en el path
sys.path.insert(0, str(Path(__file__).parent))

from gui import AulesDownloaderApp


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler("aules_sync.log", encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )
    # No registrar contraseñas ni tokens
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def main():
    setup_logging()
    app = AulesDownloaderApp()
    app.mainloop()


if __name__ == "__main__":
    main()