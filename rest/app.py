"""Adaptador REST (FastAPI): contrato de borde hacia el frontend.

Implementa `specs/001-dual-eks-grpc-infra/contracts/openapi.yaml`.
Único adaptador desplegado en v1.

Las rutas se exponen en la raíz (uso directo / pruebas) y también bajo el
prefijo `/api`, que es el que publica el ALB (`/api/*` -> backend).
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import FRONTEND_ORIGIN, configure_logging
from domain.models import DomainError, Movement
from domain.service import FinanceService


class MovementTypeModel(str, Enum):
    INCOME = "INCOME"
    EXPENSE = "EXPENSE"


class CreateMovementRequest(BaseModel):
    type: MovementTypeModel
    amount_cents: int = Field(gt=0)
    description: str = Field(default="", max_length=200)


class MovementResponse(BaseModel):
    id: str
    type: MovementTypeModel
    amount_cents: int
    description: str
    occurred_at: str


class BalanceResponse(BaseModel):
    balance_cents: int


def _to_response(movement: Movement) -> MovementResponse:
    return MovementResponse(
        id=movement.id,
        type=MovementTypeModel(movement.type.value),
        amount_cents=movement.amount_cents,
        description=movement.description,
        occurred_at=movement.occurred_at,
    )


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title="Finance API (borde REST)", version="1.0.0")
    service = FinanceService()
    app.state.service = service

    origins = ["*"] if FRONTEND_ORIGIN == "*" else [FRONTEND_ORIGIN]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(
        request: Request, exc: DomainError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.message})

    router = APIRouter()

    @router.get("/movements", response_model=list[MovementResponse])
    def list_movements() -> list[MovementResponse]:
        return [_to_response(m) for m in service.list_movements()]

    @router.post("/movements", response_model=MovementResponse, status_code=201)
    def create_movement(payload: CreateMovementRequest) -> MovementResponse:
        movement = service.create_movement(
            payload.type.value, payload.amount_cents, payload.description
        )
        return _to_response(movement)

    @router.get("/balance", response_model=BalanceResponse)
    def get_balance() -> BalanceResponse:
        return BalanceResponse(balance_cents=service.get_balance_cents())

    # Raíz (uso directo y pruebas) y borde público bajo /api (ALB).
    app.include_router(router)
    app.include_router(router, prefix="/api")

    return app


app = create_app()
