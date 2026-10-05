"""Configuración y logging compartidos por los adaptadores."""

from __future__ import annotations

import logging
import os

# Puerto del adaptador REST dentro del contenedor.
PORT = int(os.getenv("PORT", "8000"))

# Origen permitido para el frontend (CORS). En v1 el frontend llama server-side,
# así que el valor por defecto es permisivo solo para desarrollo local.
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "*")

_LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def configure_logging() -> None:
    logging.basicConfig(
        level=_LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
