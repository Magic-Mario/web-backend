"""Prueba de contrato REST contra contracts/openapi.yaml.

Se escribe antes de la implementación (debe fallar hasta que exista
`rest.app`). Valida que el adaptador exponga el contrato de borde.
"""

from __future__ import annotations

import pathlib

import yaml
from fastapi.testclient import TestClient

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
OPENAPI_PATH = (
    REPO_ROOT / "specs" / "001-dual-eks-grpc-infra" / "contracts" / "openapi.yaml"
)


def _load_spec() -> dict:
    with OPENAPI_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _client() -> TestClient:
    # Import diferido: el test debe fallar si el adaptador no existe todavía.
    from rest.app import create_app

    return TestClient(create_app())


def test_app_exposes_openapi_contract_paths() -> None:
    spec = _load_spec()
    contract_paths = set(spec["paths"].keys())

    app = _client().app
    app_paths = set(app.openapi()["paths"].keys())

    assert contract_paths <= app_paths, (
        f"faltan rutas del contrato: {contract_paths - app_paths}"
    )


def test_create_list_and_balance_roundtrip() -> None:
    client = _client()
    spec = _load_spec()
    movement_schema = spec["components"]["schemas"]["Movement"]

    created = client.post(
        "/movements",
        json={"type": "INCOME", "amount_cents": 10000, "description": "sueldo"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    for field in movement_schema["properties"]:
        assert field in body
    assert body["type"] == "INCOME"
    assert body["amount_cents"] == 10000

    client.post("/movements", json={"type": "EXPENSE", "amount_cents": 2500})

    listed = client.get("/movements")
    assert listed.status_code == 200
    assert len(listed.json()) == 2

    balance = client.get("/balance")
    assert balance.status_code == 200
    assert balance.json() == {"balance_cents": 7500}


def test_invalid_payloads_return_422() -> None:
    client = _client()

    assert client.post(
        "/movements", json={"type": "INCOME", "amount_cents": 0}
    ).status_code == 422
    assert client.post(
        "/movements", json={"type": "UNSPECIFIED", "amount_cents": 100}
    ).status_code == 422
    assert client.post(
        "/movements",
        json={"type": "INCOME", "amount_cents": 100, "description": "x" * 201},
    ).status_code == 422


def test_api_prefix_matches_root_contract() -> None:
    """/api/* es el prefijo que publica el ALB hacia el backend."""
    client = _client()

    created = client.post(
        "/api/movements",
        json={"type": "INCOME", "amount_cents": 1000, "description": "sueldo"},
    )
    assert created.status_code == 201, created.text

    assert client.get("/api/movements").status_code == 200
    assert client.get("/api/balance").json() == {"balance_cents": 1000}
