"""Adaptador REST (FastAPI): contrato de borde hacia el frontend.

Implementa `contracts/openapi.yaml` — el contrato del **juego**, que sustituyó
por completo al de finanzas (FR-009, SC-009: 0 endpoints de finanzas).

Las mismas rutas se sirven en la **raíz** (uso directo y pruebas) y bajo el
prefijo **`/api`**, que es el que publica el ALB (`/api/*` → backend). Se
monta como sub-aplicación en lugar de `include_router(prefix="/api")` porque
`openapi()` solo declara rutas `APIRoute`: así `tests/test_rest_contract.py`
(comparación exacta de rutas) sigue siendo fiel al contrato mientras el
prefijo del ALB sigue respondiendo.

Identidad anónima (FR-008): la cabecera `X-Player-Id` es un UUID v4 generado
en el navegador. Sin cuentas, sin cookies, sin autenticación (principio IV).
Las rutas de vida la exigen; `GET /health` y `GET /catalog/birth` no
(precisamente así aparecen en el contrato).
"""

from __future__ import annotations

import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from config import FRONTEND_ORIGIN, configure_logging
from domain.catalog import birth_catalog
from domain.models import (
    CauseOfDeath,
    DomainError,
    Gender,
    Granularity,
    Life,
    LifeStatus,
    PersonState,
    Relation,
)
from domain.service import GameService
from persistence.lives_repo import LivesRepo

#: `DomainError.code` → estado HTTP del contrato (`Error` / `ValidationError`).
DOMAIN_ERROR_STATUS: dict[str, int] = {
    "NOT_FOUND": 404,
    "CONFLICT": 409,
    "INVALID_ARGUMENT": 422,
}

APP_TITLE = "Life Simulator API (borde REST)"
APP_DESCRIPTION = (
    "Contrato de borde navegador→backend del simulador de vida "
    "(specs/002-simulacion-vida/contracts/openapi.yaml)."
)


# --- esquemas del contrato -------------------------------------------------


class HealthResponse(BaseModel):
    status: str


class CreateLifeRequest(BaseModel):
    """`CreateLifeRequest`: `additionalProperties: false` en el contrato."""

    model_config = ConfigDict(extra="forbid")

    gender: Gender
    country: str
    city: str
    birth_year: int
    granularity: Granularity = Granularity.YEAR


class StatsModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    health: int
    happiness: int
    intelligence: int
    looks: int


class PersonaCercanaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    relation: Relation
    state: PersonState


class LifeModel(BaseModel):
    """Vista pública de `Life`: sin cola de reserva ni contadores internos."""

    model_config = ConfigDict(extra="forbid")

    player_id: str
    gender: Gender
    country: str
    city: str
    birth_year: int
    granularity: Granularity
    age_months: int
    current_year: int
    stats: StatsModel
    status: LifeStatus
    cause_of_death: CauseOfDeath | None = None
    people: list[PersonaCercanaModel]


class BirthCountryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    cities: list[str]


class BirthCatalogModel(BaseModel):
    countries: list[BirthCountryModel]


def _life_model(life: Life) -> LifeModel:
    return LifeModel(
        player_id=life.player_id,
        gender=life.gender,
        country=life.country,
        city=life.city,
        birth_year=life.birth_year,
        granularity=life.granularity,
        age_months=life.age_months,
        current_year=life.current_year,
        stats=StatsModel(**life.stats.to_dict()),
        status=life.status,
        cause_of_death=life.cause_of_death,
        people=[
            PersonaCercanaModel(name=p.name, relation=p.relation, state=p.state)
            for p in life.people
        ],
    )


# --- identidad anónima -----------------------------------------------------


def require_player_id(x_player_id: str = Header(default="")) -> str:
    """Dependencia `X-Player-Id`: obligatoria y con forma de UUID v4 (FR-008).

    Falta o valor inválido → `422` con `{"detail": "<texto>"}`, el schema
    `ValidationError` del contrato.
    """
    if not x_player_id:
        raise HTTPException(
            status_code=422, detail="falta la cabecera obligatoria X-Player-Id"
        )
    try:
        parsed = uuid.UUID(x_player_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="X-Player-Id debe ser un UUID v4"
        ) from exc
    if parsed.version != 4:
        raise HTTPException(
            status_code=422, detail="X-Player-Id debe ser un UUID v4"
        )
    return str(parsed)


# --- aplicación -------------------------------------------------------------


def _build_app(repo: LivesRepo) -> FastAPI:
    service = GameService(repo)

    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version="1.0.0")
    app.state.repo = repo
    app.state.service = service

    origins = ["*"] if FRONTEND_ORIGIN == "*" else [FRONTEND_ORIGIN]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(
        request: Request, exc: DomainError
    ) -> JSONResponse:
        status = DOMAIN_ERROR_STATUS.get(exc.code, 422)
        return JSONResponse(status_code=status, content={"detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # El contrato define `ValidationError.detail` como **string**, no como
        # la lista que FastAPI devuelve por defecto.
        errors = exc.errors()
        first = errors[0] if errors else {}
        location = ".".join(
            str(part) for part in first.get("loc", ()) if part != "body"
        )
        message = str(first.get("msg") or "dato inválido")
        if location:
            message = f"{location}: {message}"
        return JSONResponse(status_code=422, content={"detail": message})

    # --- rutas -----------------------------------------------------------

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="health",
        summary="Readiness del adaptador REST",
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/lives",
        response_model=LifeModel,
        status_code=201,
        operation_id="createLife",
        summary="Crea una vida validando los datos de nacimiento (FR-001)",
    )
    def create_life(
        payload: CreateLifeRequest,
        player_id: str = Depends(require_player_id),
    ) -> LifeModel:
        life = service.create_life(
            player_id=player_id,
            gender=payload.gender,
            country=payload.country,
            city=payload.city,
            birth_year=payload.birth_year,
            granularity=payload.granularity,
        )
        return _life_model(life)

    @app.get(
        "/lives",
        response_model=LifeModel,
        operation_id="getLife",
        summary="Retoma la vida en curso tras recargar o reiniciar (FR-008)",
    )
    def get_life(player_id: str = Depends(require_player_id)) -> LifeModel:
        return _life_model(service.get_life(player_id))

    @app.get(
        "/catalog/birth",
        response_model=BirthCatalogModel,
        operation_id="getBirthCatalog",
        summary="Catálogo acotado de países y ciudades (FR-001)",
    )
    def get_birth_catalog() -> BirthCatalogModel:
        return BirthCatalogModel.model_validate(birth_catalog())

    return app


def create_app(repo: LivesRepo | None = None) -> FastAPI:
    """Factoría usada por `uvicorn`, por el contenedor y por las pruebas.

    `repo` se comparte entre la app de raíz y la montada en `/api`: son dos
    instancias de FastAPI con las mismas rutas y un único repositorio, de modo
    que no hay dos conexiones a DynamoDB ni dos estados.
    """
    configure_logging()
    repository = repo if repo is not None else LivesRepo()
    app = _build_app(repository)
    app.mount("/api", _build_app(repository))
    return app


app = create_app()

__all__ = ["create_app", "app", "require_player_id"]
