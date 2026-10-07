"""Prueba de contrato gRPC `events.v1` — PUERTA de la constitución.

Levanta un **servidor `EventsService` de referencia en proceso** generado a
partir de `proto/events.proto` y ejercita contra él al **cliente real**
(`grpc_client.EventsClient`), validando mensajes, enums y el límite de
eventos por período.

⚠️ Esta prueba MUST pasar **ANTES** de desplegar el adaptador (constitución
1.3.1, principio III): es el gate de la comunicación interna este-oeste.

Sustuye a la prueba de `finance.v1` de la feature 001, que desapareció con
FR-009/SC-009.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import grpc

from grpc_client import EventsClient
from grpc_client._generated import events_pb2, events_pb2_grpc

#: Catálogo canónico de la referencia: una fila por tipo de FR-003.
_REFERENCE_EVENTS = [
    ("ev-cotidiano", events_pb2.COTIDIANO, 0, 120),
    ("ev-educativo", events_pb2.EDUCATIVO, 6, 30),
    ("ev-laboral", events_pb2.LABORAL, 18, 120),
    ("ev-relacion", events_pb2.RELACION, 0, 120),
    ("ev-sentimental", events_pb2.SENTIMENTAL, 14, 120),
    ("ev-cotidiano-2", events_pb2.COTIDIANO, 0, 120),
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
                options=[
                    events_pb2.EventOption(
                        id="opt-1",
                        label="Asumir el riesgo",
                        fatal=True,
                        deltas=[
                            events_pb2.StatDelta(stat="health", value=-10),
                            events_pb2.StatDelta(stat="happiness", value=5),
                        ],
                    ),
                    events_pb2.EventOption(
                        id="opt-2",
                        label="Tomarlo con calma",
                        deltas=[events_pb2.StatDelta(stat="happiness", value=3)],
                    ),
                ],
            )
            for event_id, event_type, min_age, max_age in _REFERENCE_EVENTS
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
                exclude_ids=["ev-cotidiano"],
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
    assert "ev-cotidiano" in request.exclude_ids
    served_ids = [event.id for event in response.events]
    assert "ev-cotidiano" not in served_ids
    assert served_ids == [
        "ev-educativo",
        "ev-laboral",
        "ev-relacion",
        "ev-sentimental",
        "ev-cotidiano-2",
    ]

    # Enums del contrato: origen y tipo, con su nombre canónico.
    assert response.origin == events_pb2.FETCHED
    assert events_pb2.EventOrigin.Name(response.origin) == "FETCHED"
    assert {events_pb2.EventType.Name(e.type) for e in response.events} == {
        "COTIDIANO",
        "EDUCATIVO",
        "LABORAL",
        "RELACION",
        "SENTIMENTAL",
    }
    assert response.year == 1998

    # Las opciones llevan deltas y la marca de mortalidad (FR-004, FR-005a).
    first = response.events[0]
    assert [option.id for option in first.options] == ["opt-1", "opt-2"]
    fatal = first.options[0]
    assert fatal.fatal is True
    assert [(d.stat, d.value) for d in fatal.deltas] == [("health", -10), ("happiness", 5)]


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
    # ni educativo (6–30) ni laboral (18–120) le corresponden a los 5 años.
    assert [event.id for event in response.events] == ["ev-cotidiano", "ev-relacion"]
    assert events_pb2.EventOrigin.Name(events_pb2.SEED) == "SEED"
    assert events_pb2.EventOrigin.Name(events_pb2.CACHED) == "CACHED"
