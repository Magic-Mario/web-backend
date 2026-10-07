"""Cliente del `EventsService` (`events.v1`).

Una sola operación, `GetEvents` (FR-003): pide eventos para el año de vida en
curso, con la edad actual, los datos de nacimiento que condicionan la
simulación (FR-007), el `limit` de la granularidad y los `exclude_ids` ya
consumidos (SC-005).

El canal es **perezoso**: `grpc.insecure_channel` no abre conexión hasta la
primera llamada, así que crear un cliente nunca falla aunque el
microservicio esté caído. Quien llama decide cómo degradar (FR-003 y el edge
case *"el microservicio no responde → el avance continúa con un estado
controlado"*); este módulo solo traduce el contrato.
"""

from __future__ import annotations

from collections.abc import Sequence

import grpc

from config import EVENTS_GRPC_ADDRESS, EVENTS_GRPC_TIMEOUT

from ._generated import events_pb2, events_pb2_grpc

__all__ = ["EventsClient", "events_pb2", "events_pb2_grpc"]


class EventsClient:
    """Cliente síncrono de `events.v1`."""

    def __init__(
        self,
        address: str = EVENTS_GRPC_ADDRESS,
        timeout: float = EVENTS_GRPC_TIMEOUT,
        channel: grpc.Channel | None = None,
    ) -> None:
        self.address = address
        self.timeout = timeout
        self._channel = channel if channel is not None else grpc.insecure_channel(address)
        self._stub = events_pb2_grpc.EventsServiceStub(self._channel)

    def get_events(
        self,
        *,
        year: int,
        age_years: int,
        limit: int,
        gender: str = "",
        country: str = "",
        city: str = "",
        exclude_ids: Sequence[str] = (),
    ) -> events_pb2.GetEventsResponse:
        """`GetEvents` → hasta `limit` eventos, sin los de `exclude_ids`."""
        request = events_pb2.GetEventsRequest(
            year=year,
            age_years=age_years,
            gender=gender,
            country=country,
            city=city,
            limit=limit,
            exclude_ids=list(exclude_ids),
        )
        return self._stub.GetEvents(request, timeout=self.timeout)

    def close(self) -> None:
        self._channel.close()

    def __enter__(self) -> "EventsClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
