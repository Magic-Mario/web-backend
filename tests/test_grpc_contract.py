"""Prueba de contrato gRPC `events.v2` — PUERTA de la constitución.

Levanta un **servidor `EventsService` de referencia en proceso** generado a
partir de `proto/events.proto` y ejercita contra él al **cliente real**
(`grpc_client.EventsClient`), validando mensajes, enums y el límite de
eventos por período.

⚠️ Esta prueba MUST pasar **ANTES** de desplegar el adaptador (constitución
1.3.1, principio III): es el gate de la comunicación interna este-oeste.

Sustituye a la prueba de `finance.v1` de la feature 001, que desapareció con
FR-009/SC-009, y a la de `events.v1`, que el rediseño de 003 supersede.

Los **scores** del contrato son el punto delicado: el microservicio declara
−6…+6 y el dominio es quien los convierte en puntos (FR-009), así que aquí se
comprueba el rango y que ningún `deltas` sobreviva en el contrato.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import grpc

from grpc_client import EventsClient
from grpc_client._generated import events_pb2, events_pb2_grpc

#: Rango de un score en el contrato (FR-008/FR-018).
_SCORE_RANGE = range(-6, 7)

#: Catálogo canónico de la referencia: los 3 tipos de FR-003, los 3 sistemas
#: del resumen (FR-020) y rangos de edad que dejan fuera a un niño (FR-013).
#: (id, tipo, sistema, edad mínima, edad máxima)
_REFERENCE_EVENTS = (
    ("ev-main", events_pb2.MAIN, "work", 18, 120),
    ("ev-daily", events_pb2.DAILY, "education", 6, 30),
    ("ev-daily-2", events_pb2.DAILY, "", 0, 120),
    ("ev-rel", events_pb2.RELATIONSHIPS, "relationships", 0, 120),
    ("ev-rel-2", events_pb2.RELATIONSHIPS, "relationships", 14, 120),
)

#: Sistemas admitidos por el contrato; vacío = no participa en el resumen.
_SYSTEMS = {"", "education", "work", "relationships"}


def _options() -> list[events_pb2.EventOption]:
    """Dos opciones: una mortal con sesgo y otra suave, ambas con scores."""
    return [
        events_pb2.EventOption(
            id="opt-1",
            label="Asumir el riesgo",
            fatal=True,
            impact_skew=events_pb2.POSITIVE,
            relationship_score=3,
            scores=[
                events_pb2.StatScore(stat="health", score=-6),
                events_pb2.StatScore(stat="happiness", score=2),
            ],
        ),
        events_pb2.EventOption(
            id="opt-2",
            label="Tomarlo con calma",
            scores=[events_pb2.StatScore(stat="happiness", score=1)],
        ),
    ]


def _conditions() -> list[events_pb2.EventCondition]:
    """Una condición de estado, para comprobar que viaja (FR-017)."""
    return [
        events_pb2.EventCondition(
            kind=events_pb2.PARENTS_ALIVE, value="true"
        )
    ]


class ReferenceEventsServicer(events_pb2_grpc.EventsServiceServicer):
    """Servidor de referencia: respeta `limit` y `exclude_ids` como el real."""

    def __init__(self) -> None:
        self.requests: list[events_pb2.GetEventsRequest] = []

    def GetEvents(self, request, context):  # noqa: N802
        self.requests.append(request)
        excluded = set(request.exclude_ids)
        chosen = [
            events_pb2.LifeEvent(
                id=event_id,
                type=event_type,
                text=f"Suceso de {request.year}: {event_id}",
                min_age=min_age,
                max_age=max_age,
                source_url="https://es.wikipedia.org/",
                country=request.country,
                scope=events_pb2.YEAR,
                system=system,
                conditions=_conditions(),
                target_relation="PARTNER",
                options=_options(),
            )
            for event_id, event_type, system, min_age, max_age in _REFERENCE_EVENTS
            if event_id not in excluded
            and min_age <= request.age_years <= max_age
        ]
        return events_pb2.GetEventsResponse(
            events=chosen[: request.limit],
            origin=events_pb2.FETCHED,
            year=request.year,
        )


def _serve(servicer: ReferenceEventsServicer):
    """Levanta el servidor en un puerto efímero y devuelve (server, port)."""
    server = grpc.server(ThreadPoolExecutor(max_workers=2))
    events_pb2_grpc.add_EventsServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    return server, port


def test_get_events_contract_roundtrip_and_enums() -> None:
    """Mensajes `GetEventsRequest`/`GetEventsResponse` y enums del contrato."""
    servicer = ReferenceEventsServicer()
    server, port = _serve(servicer)
    try:
        with EventsClient(address=f"127.0.0.1:{port}", timeout=5.0) as client:
            response = client.get_events(
                year=1998,
                age_years=20,
                limit=5,
                gender="MALE",
                country="AR",
                city="Buenos Aires",
                exclude_ids=["ev-main"],
            )
    finally:
        server.stop(grace=0)

    # El backend reenvía literalmente los parámetros del personaje (FR-007).
    request = servicer.requests[0]
    assert (request.year, request.age_years, request.limit) == (1998, 20, 5)
    assert (request.gender, request.country, request.city) == (
        "MALE",
        "AR",
        "Buenos Aires",
    )

    # `exclude_ids` se recibe y se respeta (SC-005).
    assert "ev-main" in request.exclude_ids
    served_ids = [event.id for event in response.events]
    assert "ev-main" not in served_ids
    assert served_ids == ["ev-daily", "ev-daily-2", "ev-rel", "ev-rel-2"]

    # Enums del contrato: origen y tipo, con su nombre canónico (FR-003).
    assert response.origin == events_pb2.FETCHED
    assert events_pb2.EventOrigin.Name(response.origin) == "FETCHED"
    assert {events_pb2.EventType.Name(e.type) for e in response.events} == {
        "DAILY",
        "RELATIONSHIPS",
    }
    assert response.year == 1998


def test_events_carry_scope_country_system_and_state() -> None:
    """FR-002/FR-005/FR-017/FR-018/FR-020: los campos nuevos viajan."""
    servicer = ReferenceEventsServicer()
    server, port = _serve(servicer)
    try:
        with EventsClient(address=f"127.0.0.1:{port}", timeout=5.0) as client:
            response = client.get_events(year=2001, age_years=30, limit=5, country="AR")
    finally:
        server.stop(grace=0)

    for event in response.events:
        assert event.country == "AR"
        assert events_pb2.EventScope.Name(event.scope) == "YEAR"
        assert event.system in _SYSTEMS
        # `target_relation` y las condiciones son cadenas y enums del contrato.
        assert event.target_relation == "PARTNER"
        assert [c.kind for c in event.conditions] == [events_pb2.PARENTS_ALIVE]
        assert event.conditions[0].value == "true"

    # El sistema llega con el nombre canónico, listo para el resumen (FR-020).
    by_id = {event.id: event.system for event in response.events}
    assert by_id == {
        "ev-main": "work",
        "ev-daily": "education",
        "ev-daily-2": "",
        "ev-rel": "relationships",
        "ev-rel-2": "relationships",
    }


def test_options_declare_scores_not_points() -> None:
    """FR-008/FR-009: la opción envía scores en −6…+6, nunca puntos."""
    servicer = ReferenceEventsServicer()
    server, port = _serve(servicer)
    try:
        with EventsClient(address=f"127.0.0.1:{port}", timeout=5.0) as client:
            response = client.get_events(year=2001, age_years=30, limit=5)
    finally:
        server.stop(grace=0)

    first = response.events[0]

    # 2–4 opciones por evento (FR-008).
    assert 2 <= len(first.options) <= 4
    assert [option.id for option in first.options] == ["opt-1", "opt-2"]

    for option in first.options:
        assert option.scores, "toda opción declara al menos un score"
        for score in option.scores:
            assert score.score in _SCORE_RANGE, (option.id, score.stat, score.score)
        # El score de relación usa la misma escala que los de stat (FR-018).
        assert option.relationship_score in _SCORE_RANGE

    # La marca de mortalidad y el sesgo siguen siendo parte del contrato.
    fatal = first.options[0]
    assert fatal.fatal is True
    assert events_pb2.ImpactSkew.Name(fatal.impact_skew) == "POSITIVE"
    assert [(s.stat, s.score) for s in fatal.scores] == [
        ("health", -6),
        ("happiness", 2),
    ]


def test_limit_is_never_exceeded_and_seed_origin_roundtrips() -> None:
    """FR-003a: el servicio jamás devuelve más de `limit` eventos."""
    servicer = ReferenceEventsServicer()
    server, port = _serve(servicer)
    try:
        with EventsClient(address=f"127.0.0.1:{port}", timeout=5.0) as client:
            monthly = client.get_events(year=2026, age_years=30, limit=2)
            yearly = client.get_events(year=2026, age_years=30, limit=5)
    finally:
        server.stop(grace=0)

    # Máximo 2 por período mensual y 5 por anual (spec, sesión 2026-10-06).
    assert len(monthly.events) == 2
    assert len(yearly.events) == 5


def test_seed_origin_is_representable() -> None:
    """El fallback semilla es un valor legítimo del contrato (edge cases)."""
    servicer = ReferenceEventsServicer()
    server, port = _serve(servicer)
    try:
        with EventsClient(address=f"127.0.0.1:{port}", timeout=5.0) as client:
            response = client.get_events(year=2026, age_years=5, limit=2)
    finally:
        server.stop(grace=0)

    # Un niño solo recibe eventos cuyo rango de edad lo incluye (FR-013):
    # ni el del año (18–120) ni el educativo (6–30) le corresponden a los 5.
    assert [event.id for event in response.events] == ["ev-daily-2", "ev-rel"]
    assert events_pb2.EventOrigin.Name(events_pb2.SEED) == "SEED"
    assert events_pb2.EventOrigin.Name(events_pb2.CACHED) == "CACHED"
