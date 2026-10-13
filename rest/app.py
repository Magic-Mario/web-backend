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
from domain.errors import DomainError
from domain.events import EventSource, EventType, PresentedEvent
from domain.models import (
    CauseOfDeath,
    Gender,
    Granularity,
    Life,
    LifeEvent,
    LifeStatus,
    PersonState,
    Relation,
)
from domain.service import GameService, LifeSummary
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


class EventOptionModel(BaseModel):
    """`EventOption` público. **Sin `deltas`**: no se filtra la respuesta.

    El `fatal` sí viaja (el contrato lo declara y la interfaz lo pone de
    manifiesto), pero no qué cambia cada opción (data-model §2).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    fatal: bool = False


class PresentedEventModel(BaseModel):
    """Vista de navegador de `PresentedEvent`: sólo lo que se muestra."""

    model_config = ConfigDict(extra="forbid")

    id: str
    type: EventType
    text: str
    year: int | None = None
    source: EventSource
    options: list[EventOptionModel]


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
    #: Sólo `GET /lives` lo rellena: es lo que permite recargar la página
    #: y seguir con el suceso pendiente sin perderlo (SC-006).
    pending_event: PresentedEventModel | None = None


class BirthCountryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    name: str
    cities: list[str]


class BirthCatalogModel(BaseModel):
    countries: list[BirthCountryModel]


class DerivedModel(BaseModel):
    """`AdvanceResponse.derived`: la deriva natural de este período (FR-011)."""

    model_config = ConfigDict(extra="forbid")

    health: int
    happiness: int


class AdvanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    life: LifeModel
    event: PresentedEventModel
    derived: DerivedModel


class DecisionRequest(BaseModel):
    """`DecisionRequest`: `additionalProperties: false` en el contrato."""

    model_config = ConfigDict(extra="forbid")

    option_id: str


class DecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    life: LifeModel
    deltas: dict[str, int]
    dead: bool
    cause_of_death: CauseOfDeath | None = None


class SystemDigestModel(BaseModel):
    """`SystemDigest` de `LifeSummary.by_system` (FR-005)."""

    model_config = ConfigDict(extra="forbid")

    events: int
    decisions: int
    highlights: list[str]


class BySystemModel(BaseModel):
    """Los tres sistemas que exige el contrato: ni uno más ni uno menos."""

    model_config = ConfigDict(extra="forbid")

    education: SystemDigestModel
    work: SystemDigestModel
    relationships: SystemDigestModel


class LifeSummaryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_reached: int
    cause_of_death: CauseOfDeath
    by_system: BySystemModel


class EventHistoryItem(BaseModel):
    """Una fila del historial (`EVENT#<seq:06d>`) vista por el navegador.

    A diferencia de `PresentedEvent`, aquí sí viajan `deltas` y
    `option_label`: es el relato de lo **ya decidido**, no una opción que
    esté en juego. `age_years` se deriva de `age_months` para que la
    interfaz no tenga que recalcularlo.
    """

    model_config = ConfigDict(extra="forbid")

    seq: int
    period_index: int
    age_months: int
    age_years: int
    event_id: str
    type: EventType
    text: str
    option_id: str
    option_label: str
    deltas: dict[str, int]
    fatal: bool
    source: EventSource


class EventHistoryResponse(BaseModel):
    """`GET /lives/events`: el histórico completo en orden cronológico."""

    model_config = ConfigDict(extra="forbid")

    player_id: str
    count: int
    events: list[EventHistoryItem]


def _life_model(life: Life, *, with_pending: bool = False) -> LifeModel:
    """Serializa la vida para el navegador.

    `pending_event` sólo lo pide `GET /lives`: es lo que hace que recargar
    la página retome el suceso pendiente en vez de perderlo (SC-006). En
    `AdvanceResponse` el suceso ya viene en su campo `event`, así que ahí se
    devuelve `null` y las tres respuestas siguen siendo iguales entre sí.
    """
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
        pending_event=(
            _event_model(life.pending_event)
            if with_pending and life.pending_event is not None
            else None
        ),
    )


def _event_model(event: PresentedEvent) -> PresentedEventModel:
    return PresentedEventModel(
        id=event.id,
        type=event.type,
        text=event.text,
        year=event.year,
        source=event.source,
        options=[
            EventOptionModel(
                id=option.id, label=option.label, fatal=option.fatal
            )
            for option in event.options
        ],
    )


def _summary_model(summary: LifeSummary) -> LifeSummaryModel:
    return LifeSummaryModel(
        age_reached=summary.age_reached,
        cause_of_death=summary.cause_of_death,
        by_system=BySystemModel(**{
            name: SystemDigestModel(
                events=digest.events,
                decisions=digest.decisions,
                highlights=digest.highlights,
            )
            for name, digest in summary.by_system.items()
        }),
    )


def _history_item(record: LifeEvent) -> EventHistoryItem:
    """Vista de navegador de una fila ya resuelta del historial.

    `option_label` es aditivo: las filas antiguas, escritas sin etiqueta,
    se leen vacías y aquí se sustituyen por `option_id` para que la
    interfaz siempre tenga algo que enseñar.
    """
    return EventHistoryItem(
        seq=record.seq,
        period_index=record.period_index,
        age_months=record.age_months,
        age_years=record.age_months // 12,
        event_id=record.event_id,
        type=record.event_type,
        text=record.text,
        option_id=record.option_id,
        option_label=record.option_label or record.option_id,
        deltas=dict(record.deltas),
        fatal=record.fatal,
        source=record.source,
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
        # `with_pending`: si el jugador recargó con un suceso a medio decidir,
        # se lo devuelve en vez de dejarle bloqueado (SC-006).
        return _life_model(service.get_life(player_id), with_pending=True)

    @app.get(
        "/catalog/birth",
        response_model=BirthCatalogModel,
        operation_id="getBirthCatalog",
        summary="Catálogo acotado de países y ciudades (FR-001)",
    )
    def get_birth_catalog() -> BirthCatalogModel:
        return BirthCatalogModel.model_validate(birth_catalog())

    @app.post(
        "/lives/advance",
        response_model=AdvanceResponse,
        operation_id="advanceLife",
        summary=(
            "Avanza un período, aplica la deriva natural y presenta un único "
            "evento con sus opciones (FR-002, FR-003, FR-011)"
        ),
    )
    def advance_life(player_id: str = Depends(require_player_id)) -> AdvanceResponse:
        outcome = service.advance_life(player_id=player_id)
        return AdvanceResponse(
            life=_life_model(outcome.life),
            event=_event_model(outcome.event),
            derived=DerivedModel(**outcome.derived),
        )

    @app.post(
        "/lives/decision",
        response_model=DecisionResponse,
        operation_id="submitDecision",
        summary=(
            "Registra la opción elegida, aplica los deltas sobre las stats y "
            "resuelve la muerte si procede (FR-003, FR-004, FR-005)"
        ),
    )
    def submit_decision(
        payload: DecisionRequest,
        player_id: str = Depends(require_player_id),
    ) -> DecisionResponse:
        outcome = service.submit_decision(
            player_id=player_id, option_id=payload.option_id
        )
        return DecisionResponse(
            life=_life_model(outcome.life),
            deltas=outcome.deltas,
            dead=outcome.dead,
            cause_of_death=outcome.cause_of_death,
        )

    @app.get(
        "/lives/summary",
        response_model=LifeSummaryModel,
        operation_id="getSummary",
        summary=(
            "Resumen de fin de vida: edad alcanzada, causa de muerte y "
            "desglose por educación, trabajo y relaciones (FR-005)"
        ),
    )
    def get_summary(player_id: str = Depends(require_player_id)) -> LifeSummaryModel:
        # `409` mientras la vida siga en curso y `404` si no existe: las dos
        # compuertas del contrato las levanta `GameService.get_summary`.
        return _summary_model(service.get_summary(player_id=player_id))

    @app.get(
        "/lives/events",
        response_model=EventHistoryResponse,
        operation_id="getEventHistory",
        summary=(
            "Histórico cronológico de los sucesos ya vividos, con la opción "
            "elegida y sus deltas (data-model §2)"
        ),
    )
    def get_event_history(
        player_id: str = Depends(require_player_id),
    ) -> EventHistoryResponse:
        # `404` si la vida no existe (igual que `GET /lives`) y `[]` si
        # existe pero todavía no se ha resuelto ningún período.
        records = service.get_event_history(player_id=player_id)
        events = [_history_item(record) for record in records]
        return EventHistoryResponse(
            player_id=player_id, count=len(events), events=events
        )

    @app.delete(
        "/lives",
        status_code=204,
        operation_id="deleteLife",
        summary="Elimina la vida para empezar otra (FR-005)",
    )
    def delete_life(player_id: str = Depends(require_player_id)) -> None:
        service.delete_life(player_id=player_id)

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
