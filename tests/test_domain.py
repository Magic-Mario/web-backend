"""Pruebas de dominio del simulador de vida.

Sustituye por completo a la prueba de finanzas de la feature 001: el dominio
ahora es la vida, las cuatro stats y sus transiciones.

Cubre:
- las reglas puras de los modelos (T014) → **verdes desde esta tarea**;
- la **creación** de la vida (T021) → escritas *antes* de `GameService`
  (test-first, igual que `test_rest_contract.py`), así que quedaron en rojo
  hasta T024;
- la **progresión** de la vida (T056 · US2) → deriva natural por período,
  cola de reserva, límite 2/5 y granularidad fija durante toda la vida.

El avance con el microservicio de verdad vive en `tests/test_advance.py`
(T039); aquí se prueba el dominio, incluida la deriva como función pura.
Al final están las **3 vías de muerte**, la ausencia de edad máxima y el
**cálculo del resumen** de fin de vida (T066 · US3).
"""

from __future__ import annotations

import random as _random
import uuid
from dataclasses import replace

import pytest

from domain import service as domain_service
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
    LifeEvent,
    LifeStatus,
    PersonaCercana,
    PresentedEvent,
    EventOption,
    Relation,
    PersonState,
    Stats,
    STAT_NAMES,
)
from domain.service import GameService

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


# --- Progresión de la vida (T056 · US2) ------------------------------------


def _event(event_id: str) -> PresentedEvent:
    return PresentedEvent(
        id=event_id,
        type=EventType.COTIDIANO,
        text=f"Suceso {event_id} de la época",
        options=[EventOption("opt-1", "Actuar", {"health": 5})],
        year=1995,
        source=EventSource.WIKIPEDIA,
    )


def test_natural_drift_decays_health_only_when_a_year_completes() -> None:
    """FR-011: la salud baja con la edad; la felicidad fluctúa siempre."""
    from domain.service import natural_drift

    plain_month = natural_drift(
        _life(age_months=60, granularity=Granularity.MONTH), 1
    )
    assert plain_month["health"] == 0
    assert -3 <= plain_month["happiness"] <= 3

    birthday = natural_drift(
        _life(age_months=71, granularity=Granularity.MONTH), 1
    )
    assert birthday["health"] == -1
    assert -3 <= birthday["happiness"] <= 3


def test_the_health_decay_grows_with_the_age() -> None:
    """Es la deriva la que permite terminar una vida sin edad máxima."""
    from domain.service import natural_drift

    at_birth = natural_drift(_life(age_months=0), 12)
    at_twenty = natural_drift(_life(age_months=240), 12)

    assert at_birth["health"] == -1
    assert at_twenty["health"] == -2
    assert at_twenty["health"] < at_birth["health"]


def test_the_annual_health_loss_is_the_same_in_both_granularities() -> None:
    """El riesgo anualizado no depende de la granularidad (FR-011, FR-005)."""
    from domain.service import natural_drift

    yearly = natural_drift(_life(age_months=60, granularity=Granularity.YEAR), 12)
    monthly = sum(
        natural_drift(_life(age_months=60 + step), 1)["health"]
        for step in range(12)
    )

    assert yearly["health"] == monthly == -1


def test_the_event_limit_is_set_by_the_granularity() -> None:
    """FR-003a: 2 sucesos con granularidad mensual y 5 con anual."""
    from domain.service import EVENTS_PER_PERIOD

    assert EVENTS_PER_PERIOD[Granularity.MONTH] == 2
    assert EVENTS_PER_PERIOD[Granularity.YEAR] == 5


def test_reserve_pending_and_consumed_survive_a_reload() -> None:
    """data-model §1: la cola y el evento pendiente viajan en el estado."""
    life = _life(
        age_months=36,
        reserve=[_event("ev-2"), _event("ev-1")],
        pending_event=_event("ev-0"),
        consumed_ids=["ev-0"],
    )

    restored = Life.from_dict(life.to_dict())

    assert [event.id for event in restored.reserve] == ["ev-2", "ev-1"]
    assert restored.pending_event is not None
    assert restored.pending_event.id == "ev-0"
    assert restored.consumed_ids == ["ev-0"]
    # Los `deltas` se quedan en el servidor, pero no se pierden (data-model §2).
    assert restored.reserve[0].option("opt-1").deltas == {"health": 5}


def test_granularity_stays_fixed_for_the_whole_life() -> None:
    """FR-002: la granularidad no cambia ni al avanzar ni al recargar."""
    from domain.service import GameService

    service = GameService()
    player_id = str(uuid.uuid4())
    service.create_life(
        player_id=player_id,
        gender=Gender.MALE,
        country="AR",
        city="Buenos Aires",
        birth_year=1990,
        granularity=Granularity.MONTH,
    )
    try:
        for _ in range(3):
            service.advance_life(player_id=player_id)
            service.submit_decision(player_id=player_id, option_id="opt-2")

        life = service.get_life(player_id)
        assert life.granularity is Granularity.MONTH
        assert life.age_months == 3, "un salto de 12 meses rompería FR-002"
        assert life.period_index == 3
    finally:
        service.repo.delete_state(player_id)


def test_the_monthly_reserve_is_used_up_before_asking_again(
    events_service: object,
) -> None:
    """Decisión 5 con `limit = 2`: la tercera consulta sí toca la red."""
    from domain.service import GameService

    service = GameService()
    player_id = str(uuid.uuid4())
    service.create_life(
        player_id=player_id,
        gender=Gender.MALE,
        country="AR",
        city="Buenos Aires",
        birth_year=1990,
        granularity=Granularity.MONTH,
    )
    try:
        first = service.advance_life(player_id=player_id)
        assert len(first.life.reserve) == 1  # 1 presentado + 1 en reserva
        service.submit_decision(player_id=player_id, option_id="opt-2")
        assert len(events_service.requests) == 1

        second = service.advance_life(player_id=player_id)
        assert len(second.life.reserve) == 0  # se gastó sin volver a consultar
        service.submit_decision(player_id=player_id, option_id="opt-2")
        assert len(events_service.requests) == 1

        third = service.advance_life(player_id=player_id)
        assert len(third.life.reserve) == 1  # y ahora sí hubo que reponer
        assert len(events_service.requests) == 2
        assert events_service.requests[1].limit == 2
        # SC-005: nada de lo ya servido vuelve a pedirse.
        assert set(events_service.requests[1].exclude_ids) >= {
            first.event.id,
            second.event.id,
        }
    finally:
        service.repo.delete_state(player_id)


# --- Muerte: las 3 vías de FR-005 (T066 · US3) -----------------------------


class _FixedRng:
    """Ruleta fija: `random()` devuelve siempre el valor que se le pide.

    Sin ella la probabilidad real (1 % con `YEAR`, 0,08 % con `MONTH`)
    convertiría estas pruebas en una moneda al aire. El resto de métodos
    (`choice`, `randint`, …) delegan en el módulo `random` de verdad.
    """

    def __init__(self, roll: float) -> None:
        self._roll = roll

    def random(self) -> float:
        return self._roll

    def __getattr__(self, name: str):
        return getattr(_random, name)


def _kill_by_random(
    service: GameService, born, monkeypatch, granularity: str = "YEAR"
) -> str:
    """Lleva una vida a `DEAD` por la vía aleatoria, sin depender de catálogos."""
    player_id = born(granularity)
    monkeypatch.setattr(domain_service, "random", _FixedRng(0.0))
    service.advance_life(player_id=player_id)
    outcome = service.submit_decision(player_id=player_id, option_id="opt-1")
    assert outcome.dead and outcome.cause_of_death is CauseOfDeath.FATAL_RANDOM
    return player_id


def test_health_zero_ends_the_life(service: GameService, born) -> None:
    """FR-005: la salud a 0 mata aunque la opción elegida no sea mortal."""
    player_id = born()

    # La deriva baja la salud un punto por año (dos a partir de los 20) y
    # llega a 0 hacia los 35; se la ponemos a 0 para no recorrerlos todos.
    life = service.get_life(player_id)
    life.stats.health = 0
    service.repo.put_state(life)

    service.advance_life(player_id=player_id)
    outcome = service.submit_decision(player_id=player_id, option_id="opt-2")

    assert outcome.dead
    assert outcome.cause_of_death is CauseOfDeath.HEALTH_ZERO
    assert service.get_life(player_id).status is LifeStatus.DEAD


def test_a_fatal_option_ends_the_life(service: GameService, born) -> None:
    """FR-005a: la opción mortal mata en cuanto se elige."""
    player_id = born()
    service.advance_life(player_id=player_id)

    life = service.get_life(player_id)
    assert life.pending_event is not None
    pending = life.pending_event
    # Ni el evento ni sus opciones son mutables: se rehace el pendiente con
    # la primera opción marcada como mortal, que es como llega del catálogo.
    fatal_first = replace(pending.options[0], fatal=True)
    life.pending_event = replace(
        pending, options=[fatal_first, *pending.options[1:]]
    )
    service.repo.put_state(life)

    outcome = service.submit_decision(player_id=player_id, option_id=fatal_first.id)

    assert outcome.dead
    assert outcome.cause_of_death is CauseOfDeath.FATAL_OPTION
    assert outcome.life.stats.health > 0, "murió por la opción, no por la salud"


@pytest.mark.parametrize(
    ("granularity", "roll", "dies"),
    [
        # (granularidad, tirada de la ruleta, ¿muere?)
        (Granularity.YEAR, 0.005, True),  # 0,005 ≤ 0,01
        (Granularity.YEAR, 0.5, False),  # 0,5 > 0,01
        (Granularity.MONTH, 0.005, False),  # 0,005 > 0,0008
        (Granularity.MONTH, 0.0005, True),  # 0,0005 ≤ 0,0008
    ],
)
def test_the_random_roll_uses_the_probability_of_the_granularity(
    service: GameService,
    born,
    monkeypatch,
    granularity: Granularity,
    roll: float,
    dies: bool,
) -> None:
    """FR-005: el riesgo se ajusta al período para que anualicen igual."""
    player_id = born(granularity.value)
    service.advance_life(player_id=player_id)
    monkeypatch.setattr(domain_service, "random", _FixedRng(roll))

    outcome = service.submit_decision(player_id=player_id, option_id="opt-2")

    assert outcome.dead is dies, f"{granularity.value} con tirada {roll}"
    assert (outcome.cause_of_death is CauseOfDeath.FATAL_RANDOM) is dies


def test_there_is_no_age_ceiling(
    service: GameService, born, events_service: object
) -> None:
    """Ni el catálogo (máx. 120 años) ni el dominio ponen techo a la edad."""
    player_id = born()
    life = service.get_life(player_id)
    life.age_months = 130 * 12
    service.repo.put_state(life)

    outcome = service.advance_life(player_id=player_id)

    assert outcome.life.age_years == 131
    # A esa edad no queda suceso elegible → genérico en lugar de un error
    # (edge case 104, `domain/generic_events.py`).
    assert outcome.event.source is EventSource.SEED
    assert outcome.event.options


# --- Resumen de fin de vida: se calcula, no se guarda (FR-005 · T066) ------


def _write_history(
    service: GameService, player_id: str, rows: list[tuple[EventType, str]]
) -> None:
    """Escribe filas `EVENT#<seq>` con la misma forma que una partida real."""
    start = service.get_life(player_id).sequence
    for offset, (event_type, text) in enumerate(rows, start=1):
        service.repo.put_event(
            player_id,
            LifeEvent(
                seq=start + offset,
                period_index=offset,
                age_months=offset * 12,
                year=1990 + offset,
                event_id=f"ev-{offset:03d}",
                event_type=event_type,
                text=text,
                option_id="opt-1",
                deltas={},
                fatal=False,
                source=EventSource.SEED,
            ),
        )


def test_the_summary_groups_the_history_by_system(
    service: GameService, born, monkeypatch
) -> None:
    """FR-005 / T062: agrupar por `event_type`; `COTIDIANO` queda fuera."""
    player_id = _kill_by_random(service, born, monkeypatch)
    _write_history(
        service,
        player_id,
        [
            (EventType.COTIDIANO, "Un día cualquiera"),
            (EventType.EDUCATIVO, "Terminaste la primaria"),
            (EventType.EDUCATIVO, "Terminaste la universidad"),
            (EventType.LABORAL, "Tu primer empleo"),
            (EventType.RELACION, "Conociste a un amigo"),
            (EventType.SENTIMENTAL, "Conociste a tu pareja"),
        ],
    )

    summary = service.get_summary(player_id=player_id)

    assert summary.age_reached == 1
    assert summary.cause_of_death is CauseOfDeath.FATAL_RANDOM
    assert set(summary.by_system) == {"education", "work", "relationships"}

    education = summary.by_system["education"]
    assert (education.events, education.decisions) == (2, 2)
    # Lo más reciente primero: es lo que acaba de pasar en la partida.
    assert education.highlights == [
        "Terminaste la universidad",
        "Terminaste la primaria",
    ]

    assert (summary.by_system["work"].events) == 1
    # RELACIÓN y SENTIMENTAL comparten sistema.
    assert (summary.by_system["relationships"].events) == 2
    assert summary.by_system["relationships"].highlights == [
        "Conociste a tu pareja",
        "Conociste a un amigo",
    ]
    # COTIDIANO no cuenta en ningún sistema: sólo salen los tres del contrato.
    assert sum(digest.events for digest in summary.by_system.values()) == 5


def test_the_summary_highlights_stop_at_five(
    service: GameService, born, monkeypatch
) -> None:
    """`SystemDigest.highlights` declara `maxItems: 5` en el contrato."""
    player_id = _kill_by_random(service, born, monkeypatch)
    _write_history(
        service,
        player_id,
        [(EventType.EDUCATIVO, f"Etapa {index}") for index in range(7)],
    )

    digest = service.get_summary(player_id=player_id).by_system["education"]

    assert (digest.events, digest.decisions) == (7, 7)
    assert len(digest.highlights) == 5
    assert digest.highlights[0] == "Etapa 6", "deben ser los 5 más recientes"
    assert digest.highlights[-1] == "Etapa 2"
