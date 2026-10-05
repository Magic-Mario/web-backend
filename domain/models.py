"""Entidades y errores del dominio de finanzas.

Sin dependencias de framework: el mismo dominio lo consumen el adaptador REST
(FastAPI, desplegado) y el adaptador gRPC (declarado, no desplegado).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

DESCRIPTION_MAX_LENGTH = 200


class MovementType(str, Enum):
    """Tipo de movimiento. `UNSPECIFIED` no es un valor válido al crear."""

    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class DomainError(Exception):
    """Error de dominio con un `code` mapeable a INVALID_ARGUMENT."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _now_utc_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class Movement:
    """Ingreso o gasto inmutable.

    Reglas (data-model.md):
    - `type` ∈ {INCOME, EXPENSE}.
    - `amount_cents` > 0 (int64, en centavos).
    - `description` máximo 200 caracteres; vacía permitida.
    - `id` y `occurred_at` se generan al crear.
    """

    id: str
    type: MovementType
    amount_cents: int
    description: str
    occurred_at: str

    @classmethod
    def create(
        cls,
        type: MovementType | str,
        amount_cents: int,
        description: str = "",
    ) -> "Movement":
        try:
            movement_type = MovementType(type)
        except ValueError as exc:
            raise DomainError(
                "INVALID_ARGUMENT",
                "type must be INCOME or EXPENSE",
            ) from exc

        if amount_cents is None or amount_cents <= 0:
            raise DomainError("INVALID_ARGUMENT", "amount_cents must be > 0")

        if description is None:
            description = ""
        if len(description) > DESCRIPTION_MAX_LENGTH:
            raise DomainError(
                "INVALID_ARGUMENT",
                f"description must be at most {DESCRIPTION_MAX_LENGTH} characters",
            )

        return cls(
            id=str(uuid4()),
            type=movement_type,
            amount_cents=int(amount_cents),
            description=description,
            occurred_at=_now_utc_rfc3339(),
        )
