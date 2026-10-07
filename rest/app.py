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

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import FRONTEND_ORIGIN, configure_logging
from domain.models import DomainError
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


class HealthResponse(BaseModel):
    status: str


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


def _build_app(repo: LivesRepo) -> FastAPI:
    app = FastAPI(title=APP_TITLE, description=APP_DESCRIPTION, version="1.0.0")
    app.state.repo = repo

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
        location = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
        message = str(first.get("invalid") or first.get("msg") or "dato inválido")
        if location:
            message = f"{location}: {message}"
        return JSONResponse(status_code=422, content={"detail": message})

    @app.get(
        "/health",
        response_model=HealthResponse,
        operation_id="health",
        summary="Readiness del adaptador REST",
    )
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    return app


def create_app(repo: LivesRepo | None = None) -> FastAPI:
    """Factoría usada por `uvicorn`, por el contenedor y por las pruebas.

    `repo` se comparte entre la app de raíz y la montada en `/api`: son dos
    instancias de FastAPI con las mismas rutas y un único repositorio.
    """
    configure_logging()
    repository = repo if repo is not None else LivesRepo()
    app = _build_app(repository)
    app.mount("/api", _build_app(repository))
    return app


app = create_app()

__all__ = ["create_app", "app", "require_player_id"]
