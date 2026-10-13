"""Prueba de contrato REST contra `contracts/openapi.yaml` — PUERTA de la
constitución para la comunicación frontend→backend.

Se escribe **antes** de la implementación (test-first, como pide la plantilla
de tareas): hasta que US3 sirva las 8 rutas del contrato, los tests de
cobertura y de round-trip **deben fallar**. `test_contract_has_no_finance_routes`
sí pasa desde el momento en que se coloca el contrato (T009).

Este fichero sustituye por completo a la prueba de finanzas de la feature 001:
los tests viejos afirmaban justo lo contrario de lo que mide SC-009.

El repo es el source of truth de sus contratos (ver README), así que se valida
contra `contracts/openapi.yaml` propio: es hermético (funciona aunque el repo
se clone en solitario) y no depende de la profundidad de directorios. El
`parents[3]` anterior apuntaba al layout monorepo `apps/backend/tests/` y
quebró con el split de repos.
"""

from __future__ import annotations

import pathlib
import uuid

import yaml
from fastapi.testclient import TestClient

OPENAPI_PATH = pathlib.Path(__file__).resolve().parents[1] / "contracts" / "openapi.yaml"

FINANCE_TOKENS = ("balance", "movement", "finance")


def _load_spec() -> dict:
    with OPENAPI_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _client(player_id: str | None = None) -> TestClient:
    # Import diferido: el test debe fallar si el adaptador no existe todavía.
    from rest.app import create_app

    client = TestClient(create_app())
    client.headers["X-Player-Id"] = player_id or str(uuid.uuid4())
    return client


def _new_life(client: TestClient, **overrides) -> dict:
    payload = {
        "gender": "MALE",
        "country": "AR",
        "city": "Buenos Aires",
        "birth_year": 1990,
        "granularity": "YEAR",
    }
    payload.update(overrides)
    response = client.post("/lives", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_contract_has_no_finance_routes() -> None:
    """SC-009: 0 endpoints de finanzas declarados en el contrato."""
    spec = _load_spec()
    declared = " ".join(spec["paths"].keys()).lower()
    for token in FINANCE_TOKENS:
        assert token not in declared, f"el contrato aún declara finanzas: {token}"


def test_app_exposes_openapi_contract_paths() -> None:
    """Toda ruta del contrato MUST estar servida por la app (y solo las del juego)."""
    spec = _load_spec()
    contract_paths = set(spec["paths"].keys())

    app = _client().app
    app_paths = set(app.openapi()["paths"].keys())

    assert contract_paths <= app_paths, (
        f"faltan rutas del contrato: {sorted(contract_paths - app_paths)}"
    )

    # Y no puede sobrar ninguna: 0 endpoints de finanzas (SC-009).
    assert app_paths <= contract_paths, (
        f"rutas servidas que no están en el contrato: {sorted(app_paths - contract_paths)}"
    )


def test_player_id_header_is_required_and_validated() -> None:
    """FR-008: identidad anónima UUID v4 en la cabecera `X-Player-Id`."""
    from rest.app import create_app

    app = TestClient(create_app())

    # Sin cabecera -> 422 (es un parámetro requerido del contrato).
    assert app.post(
        "/lives",
        json={"gender": "MALE", "country": "AR", "city": "Buenos Aires", "birth_year": 1990},
    ).status_code == 422

    # Con un valor que no es UUID -> 422.
    app.headers["X-Player-Id"] = "no-es-un-uuid"
    assert app.post(
        "/lives",
        json={"gender": "MALE", "country": "AR", "city": "Buenos Aires", "birth_year": 1990},
    ).status_code == 422


def test_create_life_returns_initial_stats_and_parents() -> None:
    """US1 · FR-001, FR-010, FR-014: validación, stats sorteadas y padres."""
    spec = _load_spec()
    life_schema = spec["components"]["schemas"]["Life"]

    client = _client()
    life = _new_life(client)

    for field in life_schema["required"]:
        assert field in life, f"falta el campo obligatorio {field}"

    # FR-010 (D1): una tirada por stat dentro del rango, no 50 para todos.
    assert all(40 <= value <= 60 for value in life["stats"].values()), life["stats"]

    # FR-014: los padres existen desde la creación.
    relations = {p["relation"] for p in life["people"]}
    assert {"FATHER", "MOTHER"} <= relations, life["people"]

    # FR-001: dato inválido impide crear la vida.
    assert client.post(
        "/lives",
        json={"gender": "MALE", "country": "AR", "city": "Buenos Aires", "birth_year": 1890},
    ).status_code == 422
    assert client.post(
        "/lives",
        json={"gender": "MALE", "country": "AR", "city": "", "birth_year": 1990},
    ).status_code == 422


def test_create_advance_decision_roundtrip() -> None:
    """US2 · FR-002, FR-007, FR-011: el período abre un lote y se resuelve en orden."""
    client = _client()
    _new_life(client)

    advanced = client.post("/lives/advance")
    assert advanced.status_code == 200, advanced.text
    body = advanced.json()

    # FR-002: la edad avanza (la granularidad YEAR suma 12 meses).
    assert body["life"]["age_months"] == 12, body["life"]
    assert body["age_years"] == 1, body

    # FR-007: el lote trae de 1 a 3 sucesos, cada uno con 2–4 opciones.
    events = body["events"]
    assert 1 <= len(events) <= 3, events
    for event in events:
        assert event["country"], "FR-002: cada suceso declara su país"
        assert len(event["options"]) >= 2, event

    # FR-011: la deriva natural se informa por separado de los eventos.
    assert isinstance(body.get("derived"), dict), body.get("derived")
    # El avance **abre** el período, así que todavía no está completo (FR-007).
    assert body["period_complete"] is False, body
    # Y con el período abierto, avanzar otra vez es un 409.
    assert client.post("/lives/advance").status_code == 409

    # Se resuelve el lote entero: un suceso por decisión (FR-007).
    for index, event in enumerate(events):
        decided = client.post(
            "/lives/decision", json={"event_id": event["id"], "option_id": "opt-1"}
        )
        assert decided.status_code == 200, decided.text
        decision = decided.json()
        # SC-003: toda decisión cambia algo observable.
        assert decision["deltas"], decision
        assert "dead" in decision, decision
        # El período se cierra con la última decisión, y sólo con ella.
        assert decision["period_complete"] is (index == len(events) - 1), decision

    # Resuelto el período, el siguiente avance vuelve a ir a 200.
    second = client.post("/lives/advance")
    assert second.status_code == 200, second.text
    assert second.json()["life"]["age_months"] == 24, second.json()["life"]


def test_summary_gated_until_death_and_new_life_allowed() -> None:
    """US3 · FR-005, SC-004: resumen solo al morir y partida nueva sin bloqueo."""
    client = _client()
    _new_life(client)

    # Mientras vive, el resumen no existe (409, no un estado intermedio roto).
    assert client.get("/lives/summary").status_code == 409

    # Terminar la vida habilita el resumen y permite empezar de nuevo.
    assert client.delete("/lives").status_code == 204
    assert client.get("/lives").status_code == 404
    assert client.get("/lives/summary").status_code == 404

    # SC-004: nunca bloquea la interfaz — se puede crear otra vida.
    assert all(40 <= value <= 60 for value in _new_life(client)["stats"].values())


def test_api_prefix_matches_root_contract() -> None:
    """/api/* es el prefijo que publica el ALB hacia el backend."""
    client = _client()
    _new_life(client)

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/lives").status_code == 200
    assert client.post("/api/lives/advance").status_code == 200
