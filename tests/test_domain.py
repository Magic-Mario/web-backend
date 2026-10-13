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

import uuid
from dataclasses import replace

import pytest

from domain import service as domain_service
from domain.drift import random_death_roll
from domain.errors import DomainError
from domain.events import EventOption, EventSource, EventType, PresentedEvent
from domain.models import (
    BIRTH_STAT_MAX,
    BIRTH_STAT_MIN,
    INITIAL_STAT,
    RANDOM_DEATH_PROBABILITY,
    STAT_NAMES,
    CauseOfDeath,
    Gender,
    Granularity,
    Life,
    LifeEvent,
    LifeStatus,
    PersonaCercana,
    PersonState,
    Relation,
    Stats,
)
from domain.service import GameService

# --- Stats: escala 0–100 y sorteo de nacimiento (FR-004, FR-010, SC-003) --


def test_stats_default_is_the_centre_of_the_range_and_stays_in_range() -> None:
    """`Stats()` sin argumentos es el **defecto**, no el nacimiento (FR-010).

    El nacimiento es una tirada por stat (lo prueba
    `test_create_life_draws_every_stat_inside_the_birth_range`); este valor sólo
    rellena una stat ausente en una fila vieja.
    """
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
        type=EventType.DAILY,
        text="Sucede algo",
        options=[
            EventOption("opt-1", "Actuar", {"health": 5}),
            EventOption("opt-2", "Ignorar", {"happiness": -2}),
        ],
        year=1990,
        source=EventSource.SEED,
    )
    assert event.option("opt-2").scores == {"happiness": -2}
    with pytest.raises(DomainError) as exc:
        event.option("opt-99")
    assert exc.value.code == "INVALID_ARGUMENT"


# --- Creación de la vida (T021 · US1) — ROJO hasta T024 -------------------


def test_create_life_draws_every_stat_inside_the_birth_range() -> None:
    """FR-010 (D1): una tirada por stat dentro de 40–60, no un valor fijo."""
    from domain.service import GameService

    life = GameService().create_life(
        player_id=str(uuid.uuid4()),
        gender=Gender.FEMALE,
        country="ES",
        city="Madrid",
        birth_year=1990,
    )

    assert all(
        BIRTH_STAT_MIN <= value <= BIRTH_STAT_MAX
        for value in life.stats.to_dict().values()
    ), life.stats.to_dict()


def test_two_lives_do_not_born_with_the_same_stats() -> None:
    """SC-011: la variedad **entre** vidas es el objetivo de la decisión D1."""
    from domain.service import GameService

    service = GameService()
    drawn: set[tuple] = set()
    for _ in range(6):
        life = service.create_life(
            player_id=str(uuid.uuid4()),
            gender=Gender.MALE,
            country="ES",
            city="Madrid",
            birth_year=1990,
        )
        drawn.add(tuple(sorted(life.stats.to_dict().items())))
        service.repo.delete_state(life.player_id)

    assert len(drawn) > 1, "seis vidas con las mismas cuatro stats: no hay sorteo"


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
        type=EventType.DAILY,
        text=f"Suceso {event_id} de la época",
        options=[EventOption("opt-1", "Actuar", {"health": 5})],
        year=1995,
        source=EventSource.WIKIPEDIA,
    )


def test_natural_drift_only_touches_health_when_a_year_completes() -> None:
    """FR-011: la salud sólo se toma al cumplir años; la felicidad, siempre."""
    from domain.service import natural_drift

    plain_month = natural_drift(
        _life(age_months=60, granularity=Granularity.MONTH), 1
    )
    assert plain_month["health"] == 0
    assert -3 <= plain_month["happiness"] <= 3

    # 71 meses + 1 = 72: los 6 años cumplidos, ahí sí se toma la salud.
    birthday = natural_drift(
        _life(age_months=71, granularity=Granularity.MONTH), 1
    )
    assert birthday["health"] == 1
    assert -3 <= birthday["happiness"] <= 3


def test_health_recovers_while_young_and_decays_in_old_age() -> None:
    """La salud es una stat jugable: de joven sube, en la vejez decae."""
    from domain.service import natural_drift

    # Por debajo de los 40 la deriva sólo puede subir: 0→1, 20→21 y 39→40.
    assert natural_drift(_life(age_months=0), 12)["health"] == 1
    assert natural_drift(_life(age_months=240), 12)["health"] == 1
    assert natural_drift(_life(age_months=467), 1)["health"] == 1

    # A partir del pico es negativa y se acelera con los años.
    at_forty = natural_drift(_life(age_months=468), 12)["health"]
    at_sixty = natural_drift(_life(age_months=708), 12)["health"]
    at_eighty = natural_drift(_life(age_months=948), 12)["health"]

    assert (at_forty, at_sixty, at_eighty) == (-1, -2, -3)
    assert at_forty > at_sixty > at_eighty, "el decayimiento debe crecer"


def test_old_age_can_drain_health_to_zero() -> None:
    """Es la deriva de la vejez la que permite morir por salud a 0 (FR-005)."""
    from domain.service import natural_drift

    health = 100
    drained_at: int | None = None
    for age_months in range(40 * 12, 101 * 12, 12):
        health = max(0, health + natural_drift(_life(age_months=age_months), 12)["health"])
        if health == 0 and drained_at is None:
            drained_at = age_months // 12

    assert health == 0
    assert drained_at is not None and drained_at < 101, "la salud debe llegar a 0"


def test_the_annual_health_loss_is_the_same_in_both_granularities() -> None:
    """El riesgo anualizado no depende de la granularidad (FR-011, FR-005)."""
    from domain.service import natural_drift

    yearly = natural_drift(_life(age_months=948, granularity=Granularity.YEAR), 12)
    monthly = sum(
        natural_drift(_life(age_months=948 + step), 1)["health"]
        for step in range(12)
    )

    assert yearly["health"] == monthly == -3


def test_the_batch_is_a_fixed_size_whatever_the_granularity() -> None:
    """FR-007: el lote son como mucho tres sucesos, en las dos escalas."""
    from domain.service import BATCH_LIMIT

    assert BATCH_LIMIT == 3


def test_the_batch_the_cursor_and_consumed_survive_a_reload() -> None:
    """data-model §1: el lote, el cursor y lo consumido viajan en el estado."""
    life = _life(
        age_months=36,
        pending_batch=[_event("ev-1"), _event("ev-2"), _event("ev-3")],
        batch_cursor=1,
        consumed_ids=["ev-0", "ev-1"],
    )

    restored = Life.from_dict(life.to_dict())

    assert [event.id for event in restored.pending_batch] == [
        "ev-1",
        "ev-2",
        "ev-3",
    ]
    assert restored.batch_cursor == 1
    # `pending_event` es **derivado**: el del cursor, no una copia guardada.
    assert restored.pending_event is not None
    assert restored.pending_event.id == "ev-2"
    assert restored.period_complete is False
    assert restored.consumed_ids == ["ev-0", "ev-1"]
    # Los `scores` se quedan en el servidor, pero no se pierden (data-model §2).
    assert restored.pending_batch[0].option("opt-1").scores == {"health": 5}


def test_a_legacy_row_with_reserve_becomes_a_batch() -> None:
    """Migración: una fila de 002 (`reserve[]` + `pending_event`) se reordena.

    La partida a medias **no** se pierde: el suceso que estaba pendiente pasa a
    ser `pending_batch[0]` con el cursor a 0, y la antigua reserva le sigue.
    """
    legacy = _life().to_dict()
    legacy["pending_event"] = _event("ev-pending").to_dict()
    legacy["reserve"] = [_event("ev-2").to_dict(), _event("ev-3").to_dict()]
    legacy.pop("pending_batch", None)
    legacy.pop("batch_cursor", None)

    restored = Life.from_dict(legacy)

    assert [event.id for event in restored.pending_batch] == [
        "ev-pending",
        "ev-2",
        "ev-3",
    ]
    assert restored.batch_cursor == 0
    assert restored.pending_event is not None
    assert restored.pending_event.id == "ev-pending"


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
            # Cada período tiene su lote: se resuelve entero antes de avanzar.
            while not service.get_life(player_id).period_complete:
                _decide(service, player_id, "opt-2")

        life = service.get_life(player_id)
        assert life.granularity is Granularity.MONTH
        assert life.age_months == 3, "un salto de 12 meses rompería FR-002"
        assert life.period_index == 3
    finally:
        service.repo.delete_state(player_id)


def test_a_monthly_period_is_also_one_batch(events_service: object) -> None:
    """FR-007 con granularidad mensual: un lote por mes, y el límite es 3."""
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
        assert 1 <= len(first.events) <= 3
        assert len(events_service.requests) == 1
        assert events_service.requests[0].limit == 3

        # Mientras quede un suceso del lote, no se vuelve a consultar (FR-007).
        for _ in range(len(first.events) - 1):
            _decide(service, player_id, "opt-2")
        assert len(events_service.requests) == 1

        # Cerrado el período, el siguiente avance sí consulta…
        _decide(service, player_id, "opt-2")
        service.advance_life(player_id=player_id)
        assert len(events_service.requests) == 2
        # …y SC-005: nada de lo ya servido vuelve a pedirse.
        served = {event.id for event in first.events}
        assert set(events_service.requests[1].exclude_ids) >= served
    finally:
        service.repo.delete_state(player_id)


# --- Muerte: las 3 vías de FR-005 (T066 · US3) -----------------------------


def _decide(service, player_id: str, option_id: str = "opt-2"):
    """Decide el suceso pendiente (FR-007: el contrato pide su `event_id`)."""
    pending = service.get_life(player_id).pending_event
    assert pending is not None, "no hay suceso pendiente"
    return service.submit_decision(
        player_id=player_id, event_id=pending.id, option_id=option_id
    )


def _kill_by_random(
    service: GameService, born, monkeypatch, granularity: str = "YEAR"
) -> str:
    """Lleva una vida a `DEAD` por la vía aleatoria, sin depender de catálogos.

    FR-019: el azar sale de la semilla, así que la ruleta se sustituye en la
    **tirada de muerte**, que es el único punto de azar que consulta la partida.
    """
    player_id = born(granularity)
    monkeypatch.setattr(domain_service, "random_death_roll", lambda life: True)
    service.advance_life(player_id=player_id)
    outcome = _decide(service, player_id, "opt-1")
    assert outcome.dead and outcome.cause_of_death is CauseOfDeath.FATAL_RANDOM
    return player_id


def _new_dead_life(service: GameService, born) -> str:
    """Crea una vida y la deja `DEAD` sin escribir ninguna fila de historial.

    Los tests de resumen escriben su propio historial: si la vida avanzara una
    vez, ese suceso real entraría en `by_system` y ensuciaría las cuentas. Su
    sistema lo declara el microservicio, no el tipo de evento (FR-020).
    """
    player_id = born()
    life = service.get_life(player_id)
    life.status = LifeStatus.DEAD
    life.cause_of_death = CauseOfDeath.FATAL_RANDOM
    service.repo.put_state(life)
    return player_id


def test_health_zero_ends_the_life(service: GameService, born) -> None:
    """FR-005: la salud a 0 mata aunque la opción elegida no sea mortal."""
    player_id = born()

    # De joven la deriva **sube** la salud, así que la muerte por salud
    # corresponde a la vejez:3 puntos menos al cumplir 81 años (80 → -3).
    life = service.get_life(player_id)
    life.age_months = 80 * 12
    life.stats.health = 3
    service.repo.put_state(life)

    outcome_advance = service.advance_life(player_id=player_id)
    assert outcome_advance.derived["health"] == -3
    assert outcome_advance.life.stats.health == 0

    outcome = _decide(service, player_id, "opt-2")

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
    # `pending_event` es derivado, así que se sustituye en el lote.
    fatal_first = replace(pending.options[0], fatal=True)
    life.pending_batch[life.batch_cursor] = replace(
        pending, options=[fatal_first, *pending.options[1:]]
    )
    service.repo.put_state(life)

    outcome = _decide(service, player_id, fatal_first.id)

    assert outcome.dead
    assert outcome.cause_of_death is CauseOfDeath.FATAL_OPTION
    assert outcome.life.stats.health > 0, "murió por la opción, no por la salud"


@pytest.mark.parametrize("granularity", [Granularity.YEAR, Granularity.MONTH])
def test_the_random_death_rate_matches_the_probability_of_the_granularity(
    granularity: Granularity,
) -> None:
    """FR-005: 1 % por período anual, y 1⁄12 de eso por mes (anualizan igual).

    Se mide la **tasa** sobre 100 000 semillas distintas en vez de falsear una
    tirada: con FR-019 el azar ya no se sustituye por un número fijo, y la tasa
    es exactamente lo que fija la spec. Las semillas son fijas, así que el
    resultado es determinista y no una moneda al aire.
    """
    life = _life(age_months=12 * 30, granularity=granularity)
    draws = 100_000
    deaths = sum(
        1 for seed in range(draws) if random_death_roll(replace(life, seed=seed))
    )

    expected = life.random_death_probability * draws
    assert abs(deaths - expected) <= 0.2 * expected, (granularity, deaths, expected)


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
    assert outcome.events[0].source is EventSource.SEED
    assert outcome.events[0].options


# --- Resumen de fin de vida: se calcula, no se guarda (FR-005 · T066) ------


def _write_history(
    service: GameService, player_id: str, rows: list[tuple[EventType, str]]
) -> None:
    """Escribe filas `EVENT#<seq>` con la misma forma que una partida real.

    `rows` son `(tipo, sistema, enunciado)`: el tipo sigue siendo obligatorio en
    la fila, pero desde FR-020 el resumen agrupa por el **sistema** declarado.
    """
    start = service.get_life(player_id).sequence
    for offset, (event_type, system, text) in enumerate(rows, start=1):
        service.repo.put_event(
            player_id,
            LifeEvent(
                seq=start + offset,
                period_index=offset,
                age_months=offset * 12,
                year=1990 + offset,
                event_id=f"ev-{offset:03d}",
                event_type=event_type,
                system=system,
                text=text,
                option_id="opt-1",
                deltas={},
                fatal=False,
                source=EventSource.SEED,
            ),
        )


def test_the_summary_groups_the_history_by_system(
    service: GameService, born
) -> None:
    """FR-020: agrupar por el `system` que declara cada suceso; sin sistema, fuera."""
    player_id = _new_dead_life(service, born)
    _write_history(
        service,
        player_id,
        [
            (EventType.DAILY, "", "Un día cualquiera"),
            (EventType.DAILY, "education", "Terminaste la primaria"),
            (EventType.DAILY, "education", "Terminaste la universidad"),
            (EventType.MAIN, "work", "Tu primer empleo"),
            (EventType.RELATIONSHIPS, "relationships", "Conociste a un amigo"),
            (EventType.RELATIONSHIPS, "relationships", "Conociste a tu pareja"),
        ],
    )

    summary = service.get_summary(player_id=player_id)

    assert summary.age_reached == 0
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
    # Los dos sucesos de RELATIONSHIPS comparten sistema.
    assert (summary.by_system["relationships"].events) == 2
    assert summary.by_system["relationships"].highlights == [
        "Conociste a tu pareja",
        "Conociste a un amigo",
    ]
    # El suceso sin sistema no cuenta: sólo salen los tres del contrato.
    assert sum(digest.events for digest in summary.by_system.values()) == 5


def test_the_summary_highlights_stop_at_five(service: GameService, born) -> None:
    """`SystemDigest.highlights` declara `maxItems: 5` en el contrato."""
    player_id = _new_dead_life(service, born)
    _write_history(
        service,
        player_id,
        [(EventType.DAILY, "education", f"Etapa {index}") for index in range(7)],
    )

    digest = service.get_summary(player_id=player_id).by_system["education"]

    assert (digest.events, digest.decisions) == (7, 7)
    assert len(digest.highlights) == 5
    assert digest.highlights[0] == "Etapa 6", "deben ser los 5 más recientes"
    assert digest.highlights[-1] == "Etapa 2"
