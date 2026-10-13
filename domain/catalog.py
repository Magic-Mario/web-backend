"""Catálogo de nacimiento (data-model §4).

NO es una tabla DynamoDB: es una lista acotada embebida en la imagen del
backend, servida por `GET /catalog/birth` y validada al crear la vida
(FR-001). Ampliarlo es mejora futura (spec, Assumptions).

Edge cases cubiertos:
- país inexistente o fuera del catálogo → `INVALID_ARGUMENT` (422);
- ciudad que no pertenece al país elegido → 422;
- país sin ciudades disponibles → 422 (no permite continuar con datos
  inconsistentes).
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import DomainError

_CATALOG_PATH = Path(__file__).with_name("birth_catalog.json")


@lru_cache(maxsize=1)
def _load() -> dict[str, Any]:
    """Carga perezosa y cacheada: el catálogo es inmutable en runtime."""
    with _CATALOG_PATH.open(encoding="utf-8") as fh:
        catalog = json.load(fh)
    return catalog


def birth_catalog() -> dict[str, Any]:
    """Catálogo completo, listo para `GET /catalog/birth`."""
    return _load()


def countries() -> list[dict[str, Any]]:
    return _load()["countries"]


def find_country(code: str) -> dict[str, Any]:
    for country in countries():
        if country["code"] == code:
            return country
    raise DomainError("INVALID_ARGUMENT", f"país desconocido: {code}")


def validate_birth(country: str, city: str) -> None:
    """Valida `country ∈ codes` y `city ∈ cities[country]` (FR-001)."""
    if not country or not str(country).strip():
        raise DomainError("INVALID_ARGUMENT", "country es obligatorio")
    if not city or not str(city).strip():
        raise DomainError("INVALID_ARGUMENT", "city es obligatorio")

    entry = find_country(country)
    available = entry.get("cities") or []
    if not available:
        raise DomainError(
            "INVALID_ARGUMENT",
            f"país seleccionado sin ciudades disponibles: {entry['name']}",
        )
    if city not in available:
        raise DomainError(
            "INVALID_ARGUMENT",
            f"city {city!r} no pertenece a {entry['name']}",
        )
