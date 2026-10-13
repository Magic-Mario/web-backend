"""`GET /lives/events`: histórico de sucesos ya vividos (endpoint aditivo).

No cambia ninguna ruta existente: sólo añade la lectura del historial que
`persistence/lives_repo.py` ya persiste como `EVENT#<seq:06d>` y que hasta
ahora sólo consumía `GET /lives/summary` (y sólo con la vida terminada).

Se verifica la forma **exacta** de la respuesta, el orden cronológico, el
`404` cuando no hay vida (igual que `GET /lives`), el `422` de la cabecera
`X-Player-Id` y la lectura de filas antiguas escritas sin `option_label`.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from domain.events import EventSource, EventType
from domain.models import LifeEvent
from rest.app import create_app

#: Las claves exactas que el contrato declara en cada evento del histórico.
ITEM_KEYS = {
    "seq",
    "period_index",
    "age_months",
    "age_years",
    "event_id",
    "type",
    "text",
    "option_id",
    "option_label",
    "deltas",
    "fatal",
    "source",
}

BIRTH = {
    "gender": "MALE",
    "country": "AR",
    "city": "Buenos Aires",
    "birth_year": 1990,
    "granularity": "YEAR",
}


def _client(player_id: str | None = None) -> TestClient:
    client = TestClient(create_app())
    client.headers["X-Player-Id"] = player_id or str(uuid.uuid4())
    return client


def _new_life(client: TestClient) -> None:
    response = client.post("/lives", json=BIRTH)
    assert response.status_code == 201, response.text


def test_the_endpoint_requires_a_valid_player_id_header() -> None:
    """Falta o mal la cabecera → 422, como el resto de rutas de vida (FR-008)."""
    without_header = TestClient(create_app())
    assert without_header.get("/lives/events").status_code == 422

    not_a_uuid = _client()
    not_a_uuid.headers["X-Player-Id"] = "no-es-un-uuid"
    assert not_a_uuid.get("/lives/events").status_code == 422

    not_v4 = _client()
    not_v4.headers["X-Player-Id"] = str(uuid.uuid1())
    assert not_v4.get("/lives/events").status_code == 422


def test_the_endpoint_is_404_when_there_is_no_life() -> None:
    """Consistente con `GET /lives`: sin vida no hay histórico."""
    client = _client()

    response = client.get("/lives/events")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "no hay vida para este jugador"}


def test_a_new_life_answers_an_empty_history() -> None:
    """La vida existe pero aún no decidió nada: `count: 0`, no un error."""
    client = _client()
    _new_life(client)

    response = client.get("/lives/events")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "player_id": client.headers["X-Player-Id"],
        "count": 0,
        "events": [],
    }


def test_the_endpoint_returns_the_exact_shape_in_chronological_order() -> None:
    """Forma exacta del contrato, en orden de `seq` y con la opción elegida."""
    client = _client()
    player_id = client.headers["X-Player-Id"]
    _new_life(client)

    # Dos períodos resueltos: cada uno abre su lote (FR-007) y se resuelve en
    # orden, un suceso por decisión.
    presented: list[dict] = []
    for _ in range(2):
        advanced = client.post("/lives/advance")
        assert advanced.status_code == 200, advanced.text
        events = advanced.json()["events"]
        assert events, "el período siempre trae al menos un suceso"
        for event in events:
            presented.append(event)
            decided = client.post(
                "/lives/decision",
                json={"event_id": event["id"], "option_id": "opt-1"},
            )
            assert decided.status_code == 200, decided.text

    response = client.get("/lives/events")
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {"player_id", "count", "events"}
    assert body["player_id"] == player_id
    assert body["count"] == len(body["events"]) == len(presented)

    # Orden cronológico: `seq` ascendente y el mismo orden en que se sirvieron.
    assert [event["seq"] for event in body["events"]] == list(
        range(1, len(presented) + 1)
    )
    assert [event["event_id"] for event in body["events"]] == [
        event["id"] for event in presented
    ]

    first = body["events"][0]
    assert set(first) == ITEM_KEYS
    assert first["period_index"] == 1
    assert (first["age_months"], first["age_years"]) == (12, 1)
    assert first["event_id"] == "ev-001"
    assert first["type"] == "DAILY"
    assert first["text"]
    assert first["option_id"] == "opt-1"
    assert first["option_label"] == "Tomártelo con calma"
    # `opt-1` declara happiness +2 / health −1 y la curva FR-009 los convierte
    # en +8 / −4 puntos antes de recortar a 0–100.
    assert first["deltas"] == {"happiness": 8, "health": -4}
    assert first["fatal"] is False
    assert first["source"] == "WIKIPEDIA"

    # El segundo período llega con su propia edad y sin repetir ids (SC-005).
    second_period = [
        event for event in body["events"] if event["period_index"] == 2
    ]
    assert second_period, "faltan las filas del segundo período"
    assert (second_period[0]["age_months"], second_period[0]["age_years"]) == (24, 2)
    assert len({event["event_id"] for event in body["events"]}) == len(presented)

    # El mismo contenido, también bajo el prefijo del ALB.
    prefixed = client.get("/api/lives/events")
    assert prefixed.status_code == 200, prefixed.text
    assert prefixed.json() == body


def test_rows_written_without_a_label_still_answer() -> None:
    """Lectura aditiva: la fila antigua sin `option_label` no rompe nada."""
    row = LifeEvent(
        seq=1,
        period_index=1,
        age_months=12,
        year=1991,
        event_id="generic-cotidiano",
        event_type=EventType.DAILY,
        text="Un imprevisto doméstico tuerce el plan.",
        option_id="opt-1",
        deltas={"happiness": 4},
        fatal=False,
        source=EventSource.SEED,
    ).to_dict()
    row.pop("option_label")  # exactamente una fila escrita antes del campo

    assert LifeEvent.from_dict(row).option_label == ""

    # Y en el endpoint se sustituye por el propio `option_id`.
    client = _client()
    _new_life(client)
    client.app.state.repo.put_event(
        client.headers["X-Player-Id"], LifeEvent.from_dict(row)
    )

    response = client.get("/lives/events")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert body["events"][0]["option_id"] == "opt-1"
    assert body["events"][0]["option_label"] == "opt-1"
