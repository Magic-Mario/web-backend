"""Pruebas de dominio: reglas de movimiento y cálculo de saldo."""

from __future__ import annotations

import pytest

from domain.models import DomainError, Movement, MovementType
from domain.service import FinanceService


def test_valid_movement_generates_id_and_timestamp() -> None:
    movement = Movement.create(MovementType.INCOME, 1000)
    assert movement.id
    assert movement.occurred_at.endswith("Z")
    assert movement.description == ""


@pytest.mark.parametrize("amount", [0, -1, -100])
def test_amount_must_be_positive(amount: int) -> None:
    with pytest.raises(DomainError) as exc:
        Movement.create(MovementType.INCOME, amount)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_description_max_200() -> None:
    Movement.create(MovementType.EXPENSE, 100, "x" * 200)
    with pytest.raises(DomainError) as exc:
        Movement.create(MovementType.EXPENSE, 100, "x" * 201)
    assert exc.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("bad_type", ["UNSPECIFIED", "OTHER", ""])
def test_type_must_be_income_or_expense(bad_type: str) -> None:
    with pytest.raises(DomainError) as exc:
        Movement.create(bad_type, 100)
    assert exc.value.code == "INVALID_ARGUMENT"


def test_balance_is_income_minus_expense_in_creation_order() -> None:
    service = FinanceService()
    service.create_movement("INCOME", 10000, "sueldo")
    service.create_movement("EXPENSE", 2500, "comida")
    service.create_movement("INCOME", 500)

    assert service.get_balance_cents() == 8000
    assert [m.amount_cents for m in service.list_movements()] == [10000, 2500, 500]
