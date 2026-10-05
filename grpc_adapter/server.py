"""Adaptador gRPC interno (grpc.aio) sobre el dominio de finanzas.

DECLARADO para futuros microservicios internos. NO se despliega en v1
(spec FR-012; constitution, principio III).
"""

from __future__ import annotations

import grpc

from domain.models import DomainError, Movement, MovementType
from domain.service import FinanceService, get_service

from ._generated import finance_pb2, finance_pb2_grpc

_TYPE_TO_PROTO = {
    MovementType.INCOME: finance_pb2.INCOME,
    MovementType.EXPENSE: finance_pb2.EXPENSE,
}

_TYPE_FROM_PROTO = {
    finance_pb2.INCOME: MovementType.INCOME,
    finance_pb2.EXPENSE: MovementType.EXPENSE,
}


def _to_proto(movement: Movement) -> finance_pb2.Movement:
    return finance_pb2.Movement(
        id=movement.id,
        type=_TYPE_TO_PROTO[movement.type],
        amount_cents=movement.amount_cents,
        description=movement.description,
        occurred_at=movement.occurred_at,
    )


class FinanceServicer(finance_pb2_grpc.FinanceServiceServicer):
    def __init__(self, service: FinanceService | None = None) -> None:
        self._service = service or get_service()

    async def CreateMovement(self, request, context):  # noqa: N802
        try:
            movement = self._service.create_movement(
                _TYPE_FROM_PROTO.get(request.type, request.type),
                request.amount_cents,
                request.description,
            )
        except DomainError as exc:
            await context.abort(grpc.StatusCode.INVALID_ARGUMENT, exc.message)
        return finance_pb2.CreateMovementResponse(movement=_to_proto(movement))

    async def ListMovements(self, request, context):  # noqa: N802
        return finance_pb2.ListMovementsResponse(
            movements=[_to_proto(m) for m in self._service.list_movements()]
        )

    async def GetBalance(self, request, context):  # noqa: N802
        return finance_pb2.GetBalanceResponse(
            balance_cents=self._service.get_balance_cents()
        )


async def build_server(
    service: FinanceService | None = None, host: str = "[::]:0"
):
    """Crea y arranca un servidor gRPC. Devuelve (server, puerto)."""
    server = grpc.aio.server()
    finance_pb2_grpc.add_FinanceServiceServicer_to_server(
        FinanceServicer(service), server
    )
    port = server.add_insecure_port(host)
    await server.start()
    return server, port
