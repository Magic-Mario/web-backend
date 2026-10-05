"""Prueba de contrato gRPC interno (finance.v1).

El adaptador NO se despliega en v1 (spec FR-012), pero su contrato se prueba
levantando el servicer en proceso.
"""

from __future__ import annotations

import asyncio

import grpc

from grpc_adapter._generated import finance_pb2, finance_pb2_grpc
from grpc_adapter.server import build_server


def test_grpc_contract_roundtrip_and_validation() -> None:
    async def scenario() -> None:
        server, port = await build_server()
        try:
            async with grpc.aio.insecure_channel(f"localhost:{port}") as channel:
                stub = finance_pb2_grpc.FinanceServiceStub(channel)

                created = await stub.CreateMovement(
                    finance_pb2.CreateMovementRequest(
                        type=finance_pb2.INCOME,
                        amount_cents=10000,
                        description="sueldo",
                    )
                )
                assert created.movement.amount_cents == 10000
                assert created.movement.type == finance_pb2.INCOME
                assert created.movement.id

                await stub.CreateMovement(
                    finance_pb2.CreateMovementRequest(
                        type=finance_pb2.EXPENSE, amount_cents=2500
                    )
                )

                listed = await stub.ListMovements(
                    finance_pb2.ListMovementsRequest()
                )
                assert len(listed.movements) == 2

                balance = await stub.GetBalance(finance_pb2.GetBalanceRequest())
                assert balance.balance_cents == 7500

                # UNSPECIFIED y montos <= 0 son INVALID_ARGUMENT.
                try:
                    await stub.CreateMovement(
                        finance_pb2.CreateMovementRequest(
                            type=finance_pb2.MOVEMENT_TYPE_UNSPECIFIED,
                            amount_cents=100,
                        )
                    )
                    raise AssertionError("se esperaba INVALID_ARGUMENT")
                except grpc.aio.AioRpcError as exc:
                    assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT

                try:
                    await stub.CreateMovement(
                        finance_pb2.CreateMovementRequest(
                            type=finance_pb2.INCOME, amount_cents=0
                        )
                    )
                    raise AssertionError("se esperaba INVALID_ARGUMENT")
                except grpc.aio.AioRpcError as exc:
                    assert exc.code() == grpc.StatusCode.INVALID_ARGUMENT
        finally:
            await server.stop(None)

    asyncio.run(scenario())
