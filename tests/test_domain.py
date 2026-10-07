"""Pruebas de dominio del simulador de vida.

Sustituye por completo a la prueba de finanzas de la feature 001: el dominio
ahora es la vida, las cuatro stats y sus transiciones.

Cubre:
- las reglas puras de los modelos (T014) → **verdes desde esta tarea**;
- la **creación** de la vida (T021) → escritas *antes* de `GameService`
  (test-first, igual que `test_rest_contract.py`), así que quedan en rojo
  hasta T024.

Las pruebas de avance/decisión/resumen llegan con US2 y US3.
"""

from __future__ import annotations

import uuid

import pytest

from domain.models import (
    INITIAL_STAT,
    RANDOM_DEATH_PROBABILITY,
    CauseOfDeath,
    DomainError,
    EventType,
    EventSource,
    Gender,
    Granularity,
    Life,
    LifeStatus,
    PersonaCercana,
    PresentedEvent,
    EventOption,
    Relation,
    PersonState,
    Stats,
    STAT_NAMES,
)

# --- Stats: escala 0–100 y nacimiento en 50 (FR-004, FR-010, SC-003) ------


def test_stats_born_at_50_and_stay_in_range() -> None:
    stats = Stats()
    assert stats.to_dict() == {name: INITIAL_STAT for name in STAT_NAMES}


def test_stats_apply_clamps_to_zero_one_hundred() -> None:
    stats = Stats(health=95, happiness=3)
    applied = stats.apply({"health": 20, "happiness": -20})

    assert applied == {"health": 20, "happiness": -20}
    assert stats.health == 100
    assert stats.happiness == 0


def test_stats_apply_rejects_unknown_stat() -> None:
    with pytest.raises(DomainError) as exc:
        Stats().apply({"money": 10})
    assert exc.value.code == "INVALID_ARGUMENT"


# --- PersonaCercana (FR-014) ----------------------------------------------


def test_parents_are_alive_at_birth() -> None:
    father = PersonaCercana("Juan", Relation.FATHER)
    mother = PersonaCercana("María", Relation.MOTHER)
    assert father.state is PersonState.ALIVE
    assert mother.state is PersonState.ALIVE


def test_together_and_separated_only_for_partner() -> None:
    PersonaCercana("Ana", Relation.PARTNER, PersonState.TOGETHER)
    with pytest.raises(DomainError) as exc:
        PersonaCercana("Juan", Relation.FATHER, PersonState.SEPARATED)
    assert exc.value.code == "INVALID_ARGUMENT"


# --- Vida: derivados (data-model §1, FR-002, FR-005, FR-007) --------------


def _life(**overrides) -> Life:
    payload = dict(
        player_id=str(uuid.uuid4()),
        gender=Gender.MALE,
        country="AR",
        city="Buenos Aires",
        birth_year=1990,
        granularity=Granularity.YEAR,
    )
    payload.update(overrides)
    return Life(**payload)


def test_current_year_follows_age_and_birth_year() -> None:
    life = _life(age_months=36)
    assert life.age_years == 3
    assert life.current_year == 1993


def test_period_index_matches_granularity() -> None:
    assert _life(age_months=36, granularity=Granularity.YEAR).period_index == 3
    assert _life(age_months=36, granularity=Granularity.MONTH).period_index == 36


def test_random_death_probability_per_granularity() -> None:
    """FR-005: 1% por período anual y 0,08% por período mensual."""
    assert RANDOM_DEATH_PROBABILITY[Granularity.YEAR] == 0.01
    assert RANDOM_DEATH_PROBABILITY[Granularity.MONTH] == 0.0008
    assert _life(granularity=Granularity.MONTH).random_death_probability == 0.0008


def test_dead_life_requires_a_cause_of_death() -> None:
    with pytest.raises(DomainError):
        _life(status=LifeStatus.DEAD)
    assert (
        _life(status=LifeStatus.DEAD, cause_of_death=CauseOfDeath.HEALTH_ZERO).status
        is LifeStatus.DEAD
    )


# --- Evento presentado (FR-003, FR-004) -----------------------------------


def test_event_option_must_belong_to_the_event() -> None:
    event = PresentedEvent(
        id="1990-x",
        type=EventType.COTIDIANO,
        text="Sucede algo",
        options=[
            EventOption("opt-1", "Actuar", {"health": 5}),
            EventOption("opt-2", "Ignorar", {"happiness": -2}),
        ],
        year=1990,
        source=EventSource.SEED,
    )
    assert event.option("opt-2").deltas == {"happiness": -2}
    with pytest.raises(DomainError) as exc:
        event.option("opt-99")
    assert exc.value.code == "INVALID_ARGUMENT"


# --- Creación de la vida (T021 · US1) — ROJO hasta T024 -------------------


def test_create_life_sets_the_initial_stats_to_50() -> None:
    """FR-010: idénticas para todos, sea cual sea el nacimiento."""
    from domain.service import GameService

    life = GameService().create_life(
        player_id=str(uuid.uuid4()),
        gender=Gender.FEMALE,
        country="ES",
        city="Madrid",
        birth_year=1990,
    )

    assert life.stats.to_dict() == {
        "health": 50,
        "happiness": 50,
        "intelligence": 50,
        "looks": 50,
    }


def test_create_life_includes_both_parents() -> None:
    """FR-014: los padres existen desde la creación."""
    from domain.service import GameService

    life = GameService().create_life(
        player_id=str(uuid.uuid4()),
        gender=Gender.MALE,
        country="AR",
        city="Buenos Aires",
        birth_year=1990,
    )

    relations = {person.relation for person in life.people}
    assert {"FATHER", "MOTHER"} <= relations
    assert all(person.name for person in life.people)


def test_create_life_defaults_to_yearly_granularity() -> None:
    """FR-002: `YEAR` es el valor por defecto y `age_months` empieza en 0."""
    from domain.service import GameService

    life = GameService().create_life(
        player_id=str(uuid.uuid4()),
        gender=Gender.MALE,
        country="AR",
        city="Buenos Aires",
        birth_year=2000,
    )

    assert life.granularity is Granularity.YEAR
    assert life.age_months == 0
    assert life.status is LifeStatus.ALIVE


@pytest.mark.parametrize("birth_year", [1899, 2100])
def test_create_life_rejects_birth_year_out_of_range(birth_year: int) -> None:
    """FR-001: `1900 ≤ birth_year ≤ año actual`."""
    from domain.service import GameService

    with pytest.raises(DomainError) as exc:
        GameService().create_life(
            player_id=str(uuid.uuid4()),
            gender=Gender.MALE,
            country="AR",
            city="Buenos Aires",
            birth_year=birth_year,
        )
    assert exc.value.code == "INVALID_ARGUMENT"
