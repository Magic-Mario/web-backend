"""Error de dominio, compartido por todas las capas del backend.

Vive en su propio módulo para que el vocabulario de eventos (`events.py`) y las
entidades de vida (`models.py`) puedan lanzarlo sin depender el uno del otro:
`events` necesita `DomainError`, y `models` necesita `events`.

Fuente: `specs/002-simulacion-vida/spec.md` (códigos de error del contrato).
"""

from __future__ import annotations


class DomainError(Exception):
    """Error de dominio con un `code` mapeable a un estado HTTP.

    `rest/app.py` traduce `NOT_FOUND`→404, `CONFLICT`→409 e
    `INVALID_ARGUMENT`→422, que son exactamente los códigos del contrato.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
