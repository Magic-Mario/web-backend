"""Nombres de pila con los que nacen los padres (FR-014).

FR-014 pide **nombre**, no identidad: los padres son dos personas cercanas con
nombre y estado, y aquí está el repertorio del que sale el suyo. La elección no
es azar del proceso: se deriva de la semilla de la vida (FR-019), así que la
misma vida nace siempre con los mismos padres.

Los nombres van **sin tildes de más ni apellidos** a propósito: son de pila y
españoles, que es el idioma del contenido generado.
"""

from __future__ import annotations

FATHER_NAMES: tuple[str, ...] = (
    "Antonio",
    "Carlos",
    "Daniel",
    "Diego",
    "Francisco",
    "Javier",
    "Jorge",
    "Luis",
    "Manuel",
    "Miguel",
    "Pedro",
    "Rafael",
)

MOTHER_NAMES: tuple[str, ...] = (
    "Ana",
    "Carmen",
    "Clara",
    "Elena",
    "Isabel",
    "Laura",
    "Lucía",
    "María",
    "Marta",
    "Patricia",
    "Rocío",
    "Sofía",
)

__all__ = ["FATHER_NAMES", "MOTHER_NAMES"]
