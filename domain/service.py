"""Servicio de dominio de finanzas.

Almacenamiento en memoria (una réplica), propiedad exclusiva del backend.
Sin base de datos compartida: constitution, principio III; research, Decisión 5.
"""

from __future__ import annotations

from .models import Movement, MovementType


class FinanceService:
    def __init__(self) -> None:
        self._movements: list[Movement] = []

    def create_movement(
        self,
        type: MovementType | str,
        amount_cents: int,
        description: str = "",
    ) -> Movement:
        movement = Movement.create(type, amount_cents, description)
        self._movements.append(movement)
        return movement

    def list_movements(self) -> list[Movement]:
        """Devuelve los movimientos en orden de creación."""
        return list(self._movements)

    def get_balance_cents(self) -> int:
        """balance_cents = suma(INCOME) − suma(EXPENSE)."""
        total = 0
        for movement in self._movements:
            if movement.type is MovementType.INCOME:
                total += movement.amount_cents
            else:
                total -= movement.amount_cents
        return total


# Instancia única compartida por la app (el backend corre una réplica en v1).
_service = FinanceService()


def get_service() -> FinanceService:
    return _service
