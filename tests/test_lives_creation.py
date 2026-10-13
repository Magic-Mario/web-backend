"""US1 · Creación del personaje (T022).

Escrito **antes** de la implementación (test-first): rojo hasta T024/T025.

Cubre el contrato de `POST /lives`, `GET /lives` y `GET /catalog/birth`:
validación de nacimiento (FR-001), stats sorteadas en 40–60 (FR-010), padres (FR-014),
vida única por jugador (FR-008) y el catálogo embebido.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

VALID_BIRTH = {
    "gender": "MALE",
    "country": "AR",
    "city": "Buenos Aires",
    "birth_year": 1990,
    "granularity": "YEAR",
}


def _client(player_id: str | None = None) -> TestClient:
    from rest.app import create_app

    client = TestClient(create_app())
    client.headers["X-Player-Id"] = player_id or str(uuid.uuid4())
    return client


def test_valid_creation_returns_initial_stats_and_parents() -> None:
    """FR-010 + FR-014: 4 stats sorteadas en 40–60 y al menos padre y madre."""
    client = _client()

    response = client.post("/lives", json=VALID_BIRTH)
    assert response.status_code == 201, response.text

    life = response.json()
    # FR-010 (D1): una tirada por stat dentro del rango, no 50 para todos.
    assert all(40 <= value <= 60 for value in life["stats"].values()), life["stats"]
    assert set(life["stats"]) == {"health", "happiness", "intelligence", "looks"}
    relations = {person["relation"] for person in life["people"]}
    assert {"FATHER", "MOTHER"} <= relations, life["people"]
    assert all(person["name"] for person in life["people"])
    assert all(person["state"] == "ALIVE" for person in life["people"])


def test_created_life_echoes_the_birth_data() -> None:
    """US1 · escenario 1: el perfil refleja los datos elegidos."""
    client = _client()

    created = client.post(
        "/lives",
        json={
            "gender": "FEMALE",
            "country": "ES",
            "city": "Madrid",
            "birth_year": 1985,
            "granularity": "MONTH",
        },
    )
    assert created.status_code == 201, created.text
    life = created.json()

    assert life["gender"] == "FEMALE"
    assert life["country"] == "ES"
    assert life["city"] == "Madrid"
    assert life["birth_year"] == 1985
    assert life["granularity"] == "MONTH"
    assert life["age_months"] == 0
    assert life["current_year"] == 1985
    assert life["status"] == "ALIVE"
    assert life["cause_of_death"] is None


def test_yearly_granularity_is_the_default() -> None:
    """FR-002: `YEAR` es el valor por defecto si no se envía granularidad."""
    client = _client()

    created = client.post(
        "/lives", json={k: v for k, v in VALID_BIRTH.items() if k != "granularity"}
    )
    assert created.status_code == 201, created.text
    assert created.json()["granularity"] == "YEAR"


def test_missing_or_invalid_birth_data_is_rejected() -> None:
    """FR-001 · escenario 2: señalizar el campo inválido con 422."""
    client = _client()

    cases = [
        {**VALID_BIRTH, "city": ""},  # campo vacío
        {**VALID_BIRTH, "gender": ""},  # campo vacío
        {**VALID_BIRTH, "birth_year": 1899},  # fuera de rango
        {**VALID_BIRTH, "birth_year": 2100},  # fuera de rango (año actual)
        {k: v for k, v in VALID_BIRTH.items() if k != "country"},  # ausente
        {k: v for k, v in VALID_BIRTH.items() if k != "birth_year"},  # ausente
        {**VALID_BIRTH, "country": "ZZ"},  # fuera del catálogo
        {**VALID_BIRTH, "city": "Ciudad Inexistente"},  # no pertenece al país
    ]
    for payload in cases:
        response = client.post("/lives", json=payload)
        assert response.status_code == 422, (payload, response.status_code)
        # El contrato define `ValidationError.detail` como string.
        assert isinstance(response.json()["detail"], str), response.text


def test_header_player_id_is_required_and_must_be_uuid_v4() -> None:
    """FR-008: identidad anónima; sin cabecera o inválida → 422."""
    from rest.app import create_app

    client = TestClient(create_app())
    assert client.post("/lives", json=VALID_BIRTH).status_code == 422

    client.headers["X-Player-Id"] = "no-es-un-uuid"
    assert client.post("/lives", json=VALID_BIRTH).status_code == 422

    # Un UUID v1 tampoco vale: la spec pide v4.
    client.headers["X-Player-Id"] = str(uuid.uuid1())
    assert client.post("/lives", json=VALID_BIRTH).status_code == 422


def test_second_life_for_the_same_player_conflicts() -> None:
    """FR-008: una única partida por jugador en v1 → 409."""
    client = _client()

    assert client.post("/lives", json=VALID_BIRTH).status_code == 201
    conflict = client.post("/lives", json=VALID_BIRTH)
    assert conflict.status_code == 409, conflict.text
    assert isinstance(conflict.json()["detail"], str)


def test_life_can_be_resumed_after_reload() -> None:
    """FR-008 / SC-006: la vida vive en DynamoDB, no en el proceso."""
    client = _client()
    client.post("/lives", json=VALID_BIRTH)

    resumed = client.get("/lives")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["birth_year"] == 1990

    # Otra identidad no tiene vida todavía.
    other = _client()
    assert other.get("/lives").status_code == 404
    assert other.get("/lives").json()["detail"]


def test_birth_catalog_is_served() -> None:
    """FR-001: catálogo acotado de países y ciudades (no es una tabla)."""
    client = _client()

    response = client.get("/catalog/birth")
    assert response.status_code == 200, response.text

    countries = response.json()["countries"]
    assert countries, "el catálogo no puede estar vacío"
    codes = {country["code"] for country in countries}
    assert {"AR", "ES"} <= codes
    for country in countries:
        assert country["name"]
        assert isinstance(country["cities"], list)
