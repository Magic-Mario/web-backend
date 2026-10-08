"""US3 · Cierre de la vida: resumen, muerte y nueva partida (T061).

Escrito antes de `GameService.get_summary` (T062) y de las rutas
`GET /lives/summary` / `DELETE /lives` (T063): rojo hasta que las dos
queden hechas, al igual que el resto de la suite de esta especificación.

Dos apuntes sobre cómo se llega a cada estado:

- **La muerte es determinista.** Sustituye la ruleta de `domain.service`
  por otra que siempre sale mortal (`0.0 ≤ 0,01`, FR-005). Con un 1 % real
  la prueba sería una moneda al aire; el resto de la suite convive con la
  ruleta no mortal de `tests/conftest.py`.
- **25 rondas no es un número al azar.** Con el catálogo de referencia,
  `LABORAL` sólo empieza a salir a partir de los 18 años y el quinto lote
  (`limit = 5`) llega en el avance 21: a las 25 rondas el historial ya
  tiene eventos de los tres sistemas agrupados.
"""

from __future__ import annotations

import random as _random
import uuid

from fastapi.testclient import TestClient

from domain import service as domain_service
from domain.models import CauseOfDeath
from domain.service import GameService

#: Rondas avanzar → decidir antes de matar (ver doc del módulo).
ROUNDS = 25


class _AlwaysFatalRng:
    """Ruleta que siempre sale mortal: `random()` devuelve `0.0`."""

    def random(self) -> float:
        return 0.0

    def __getattr__(self, name: str):
        # El resto de métodos (`choice`, `randint`) siguen siendo aleatorios.
        return getattr(_random, name)


def _client(player_id: str) -> TestClient:
    # Import diferido: el test debe fallar si el adaptador no existe todavía.
    from rest.app import create_app

    client = TestClient(create_app())
    client.headers["X-Player-Id"] = player_id
    return client


def _play_until_death(
    service: GameService, player_id: str, monkeypatch
) -> None:
    """24 rondas sin matar y la 25.ª ya con la ruleta mortal puesta."""
    for _ in range(ROUNDS - 1):
        service.advance_life(player_id=player_id)
        service.submit_decision(player_id=player_id, option_id="opt-2")

    monkeypatch.setattr(domain_service, "random", _AlwaysFatalRng())
    service.advance_life(player_id=player_id)
    outcome = service.submit_decision(player_id=player_id, option_id="opt-1")

    assert outcome.dead, "la vida debía morir en la 25.ª decisión"
    assert outcome.cause_of_death is CauseOfDeath.FATAL_RANDOM


# --- la compuerta: resumen sólo al morir (FR-005, SC-004) -------------------


def test_summary_is_409_while_the_life_is_running(born) -> None:
    assert _client(born()).get("/lives/summary").status_code == 409


def test_summary_is_404_when_there_is_no_life() -> None:
    assert _client(str(uuid.uuid4())).get("/lives/summary").status_code == 404


def test_the_summary_after_death_reports_age_cause_and_systems(
    service: GameService, born, monkeypatch
) -> None:
    player_id = born()
    _play_until_death(service, player_id, monkeypatch)

    response = _client(player_id).get("/lives/summary")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["age_reached"] == ROUNDS, body
    assert body["cause_of_death"] == "FATAL_RANDOM", body

    systems = body["by_system"]
    assert set(systems) == {"education", "work", "relationships"}, systems

    for name, digest in systems.items():
        # Cada evento presentado tuvo exactamente una decisión (FR-003).
        assert digest["events"] == digest["decisions"], name
        assert 0 < len(digest["highlights"]) <= 5, name

    assert systems["education"]["events"] >= 1, "faltan eventos EDUCATIVO"
    assert systems["work"]["events"] >= 1, "faltan eventos LABORAL"
    assert systems["relationships"]["events"] >= 1, "faltan RELACIÓN/SENTIMENTAL"


# --- una vida muerta sólo admite resumen y borrado -------------------------


def test_a_dead_life_only_answers_summary_and_delete(
    service: GameService, born, monkeypatch
) -> None:
    """data-model §1: `DEAD` no admite `advance` ni `decision`."""
    player_id = born()
    _play_until_death(service, player_id, monkeypatch)
    client = _client(player_id)

    assert client.post("/lives/advance").status_code == 409
    assert (
        client.post("/lives/decision", json={"option_id": "opt-1"}).status_code
        == 409
    )
    assert client.delete("/lives").status_code == 204


def test_the_life_can_be_deleted_and_started_again(born) -> None:
    """SC-004: borrar y volver a empezar nunca bloquea la interfaz."""
    client = _client(born())

    assert client.delete("/lives").status_code == 204
    assert client.get("/lives").status_code == 404
    assert client.get("/lives/summary").status_code == 404

    created = client.post(
        "/lives",
        json={
            "gender": "MALE",
            "country": "AR",
            "city": "Buenos Aires",
            "birth_year": 1990,
            "granularity": "YEAR",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["stats"]["health"] == 50
