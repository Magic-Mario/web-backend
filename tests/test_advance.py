"""US2 · Avance de período con eventos y decisiones (T039).

Escrito **antes** de la implementación (test-first, como pide la plantilla
de tareas): rojo hasta `advance_life` (T052) y `submit_decision` (T053).

Habla con el microservicio `events.v1` de referencia que levanta
`tests/conftest.py` en proceso, y persiste contra DynamoDB/Floci de verdad.

Cubre lo que pide T039:
- el salto de edad de cada granularidad (`YEAR` +12, `MONTH` +1) y
  **exactamente un evento** por período (FR-002, FR-003);
- `derived`, la deriva natural, y los `deltas` de la decisión aplicados
  **por encima** de ella (FR-011);
- ninguna opción sin `deltas` (SC-003);
- el `limit` de 5 (anual) / 2 (mensual) y el resto del lote en reserva
  (FR-003a, Decisión 5);
- `reserve[]` reutilizada sin volver a consultar;
- `consumed_ids[]` sin repetir en 20 avances (SC-005);
- el edge case *"el microservicio no responde → el avance continúa"*.
"""

from __future__ import annotations

from typing import Callable

import pytest

from domain.models import STAT_NAMES, DomainError, EventSource, PresentedEvent
from domain.service import GameService
from grpc_client.events import EventsClient
from persistence.lives_repo import LivesRepo

# `service`, `born` y `events_service` los define `tests/conftest.py`: los
# comparten `test_advance.py`, `test_summary.py` y `test_domain.py`.


def _decide(service: GameService, player_id: str) -> object:
    return service.submit_decision(player_id=player_id, option_id="opt-1")


# --- edad y un solo evento por período (FR-002, FR-003) --------------------


def test_year_granularity_jumps_twelve_months_and_presents_one_event(
    service: GameService, born: Callable[..., str], events_service: object
) -> None:
    player_id = born("YEAR")

    first = service.advance_life(player_id=player_id)

    assert first.life.age_months == 12
    assert first.life.current_year == 1991
    assert first.life.period_index == 1
    assert isinstance(first.event, PresentedEvent)
    assert first.event.text and len(first.event.options) >= 2

    # Un único evento por período: el que está pendiente de decisión.
    assert first.life.pending_event is not None
    assert first.life.pending_event.id == first.event.id

    # El resto del lote devuelto queda en reserva, sin presentarse (Decisión 5).
    assert len(first.life.reserve) == 4
    assert first.event.id not in {event.id for event in first.life.reserve}
    assert len(events_service.requests) == 1
    assert events_service.requests[0].limit == 5

    # Persiste: recargar la partida devuelve el mismo estado (FR-008, SC-006).
    assert service.get_life(player_id).age_months == 12


def test_month_granularity_advances_one_month_per_event(
    service: GameService, born: Callable[..., str], events_service: object
) -> None:
    player_id = born("MONTH")

    outcome = service.advance_life(player_id=player_id)

    assert outcome.life.age_months == 1
    assert outcome.life.current_year == 1990
    assert outcome.life.period_index == 1
    assert len(events_service.requests) == 1
    assert events_service.requests[0].limit == 2


def test_a_period_cannot_be_advanced_twice_without_deciding(
    service: GameService, born: Callable[..., str]
) -> None:
    """No se pierde el evento pendiente: primero se decide (FR-003)."""
    player_id = born()
    service.advance_life(player_id=player_id)

    with pytest.raises(DomainError) as error:
        service.advance_life(player_id=player_id)

    assert error.value.code == "CONFLICT"


# --- deriva natural y decisiones (FR-011, SC-003) --------------------------


def test_the_drift_is_reported_apart_and_the_decision_sits_on_top_of_it(
    service: GameService, born: Callable[..., str]
) -> None:
    player_id = born()
    before = service.get_life(player_id)

    outcome = service.advance_life(player_id=player_id)

    # `derived` es la deriva natural de este período, no la del evento.
    assert set(outcome.derived) == {"health", "happiness"}
    assert outcome.derived["health"] <= 0
    assert -3 <= outcome.derived["happiness"] <= 3
    assert outcome.life.stats.health == before.stats.health + outcome.derived["health"]
    assert (
        outcome.life.stats.happiness
        == before.stats.happiness + outcome.derived["happiness"]
    )

    # La decisión pesa POR ENCIMA de la deriva (FR-011).
    decision = _decide(service, player_id)
    assert decision.deltas["happiness"] == 5
    after = service.get_life(player_id)
    assert (
        after.stats.happiness
        == outcome.life.stats.happiness + decision.deltas["happiness"]
    )
    assert after.stats.health == outcome.life.stats.health + decision.deltas["health"]


def test_no_offered_option_is_left_without_deltas(
    service: GameService, born: Callable[..., str]
) -> None:
    """SC-003: 100 % de las decisiones con cambio observable."""
    player_id = born()
    event = service.advance_life(player_id=player_id).event

    assert len(event.options) >= 2
    for option in event.options:
        assert option.deltas, f"{option.id} no cambia ninguna stat"
        assert set(option.deltas) <= set(STAT_NAMES)


# --- límite por período y cola de reserva (FR-003a, Decisión 5) -----------


def test_a_new_period_only_fetches_events_when_the_reserve_is_empty(
    service: GameService, born: Callable[..., str], events_service: object
) -> None:
    player_id = born("YEAR")

    for _ in range(5):
        service.advance_life(player_id=player_id)
        _decide(service, player_id)

    # 5 avances con `limit = 5`: una única consulta al microservicio.
    assert len(events_service.requests) == 1

    service.advance_life(player_id=player_id)
    assert len(events_service.requests) == 2
    assert events_service.requests[1].limit == 5


def test_consumed_ids_never_repeat_an_event_in_twenty_advances(
    service: GameService, born: Callable[..., str], events_service: object
) -> None:
    """SC-005: 20 avances sin repetir indefinidamente el mismo suceso."""
    player_id = born()
    presented: list[str] = []
    requests_before = 0

    for _ in range(20):
        outcome = service.advance_life(player_id=player_id)

        # Toda consulta nueva va con lo ya servido en `exclude_ids`.
        if len(events_service.requests) > requests_before:
            latest = events_service.requests[-1]
            assert set(latest.exclude_ids) >= set(presented)
            assert latest.limit == 5
        requests_before = len(events_service.requests)

        presented.append(outcome.event.id)
        # `opt-2` no toca la salud: si en 20 avances eligiéramos la opción
        # que resta 2 de salud acabaríamos muriendo por derivación (FR-005).
        service.submit_decision(player_id=player_id, option_id="opt-2")

    assert len(presented) == 20
    assert len(set(presented)) == 20, "SC-005: un suceso repetido"
    assert len(events_service.requests) > 1, "la cola se debió reponer"


def test_the_backend_sends_the_birth_data_that_conditions_the_simulation(
    service: GameService, born: Callable[..., str], events_service: object
) -> None:
    """FR-007: año de vida, edad, sexo, país y ciudad viajan en la petición."""
    player_id = born()
    service.advance_life(player_id=player_id)

    request = events_service.requests[0]
    assert (request.year, request.age_years) == (1991, 1)
    assert (request.gender, request.country, request.city) == (
        "MALE",
        "AR",
        "Buenos Aires",
    )


# --- edge case: el microservicio no responde -------------------------------


def test_the_advance_survives_the_events_service_being_down(
    born: Callable[..., str],
) -> None:
    """Edge case de la spec: nunca un error que bloquee la partida."""
    player_id = born()
    unreachable = GameService(
        LivesRepo(), events=EventsClient(address="127.0.0.1:1", timeout=0.2)
    )

    outcome = unreachable.advance_life(player_id=player_id)

    assert outcome.event.source is EventSource.SEED
    assert outcome.event.text and len(outcome.event.options) >= 2
    # La deriva natural se aplica aunque no haya suceso de época (FR-011).
    assert set(outcome.derived) == {"health", "happiness"}
    assert unreachable.get_life(player_id).age_months == 12


# --- SC-006: recargar sin perder el progreso -------------------------------


def test_reloading_with_an_undecided_event_resumes_it(
    service: GameService, born: Callable[..., str]
) -> None:
    """`GET /lives` devuelve `pending_event`; sin él, recargar deja bloqueado.

    Se prueba en el adaptador REST porque ahí es donde se expone el campo
    (el dominio ya lo guarda): si `GET /lives` no lo devolviera, el jugador
    que recargara a mitad de decisión no podría ni decidir ni avanzar —
    `POST /lives/advance` responde `409` mientras haya algo pendiente.
    """
    from fastapi.testclient import TestClient

    from rest.app import create_app

    player_id = born()
    client = TestClient(create_app())
    client.headers["X-Player-Id"] = player_id

    try:
        advanced = client.post("/lives/advance")
        assert advanced.status_code == 200, advanced.text
        event_id = advanced.json()["event"]["id"]

        # El jugador recarga la página a mitad del período.
        resumed = client.get("/lives")
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["pending_event"]["id"] == event_id

        # Y mientras no decida, no se le deja avanzar de largo el suceso.
        assert client.post("/lives/advance").status_code == 409

        decided = client.post("/lives/decision", json={"option_id": "opt-1"})
        assert decided.status_code == 200, decided.text

        # Resuelto: nada pendiente y el siguiente avance vuelve a ir a 200.
        assert client.get("/lives").json()["pending_event"] is None
        assert client.post("/lives/advance").status_code == 200
    finally:
        service.repo.delete_state(player_id)
