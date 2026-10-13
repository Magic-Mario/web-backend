"""Fixtures compartidas de la suite del backend (US2, T039).

⚠️ **El orden de este fichero importa**: `config.py` lee `EVENTS_GRPC_ADDRESS`
**al importarse**, así que la dirección del microservicio se fija antes de
importar ningún módulo del proyecto.

La suite entera —incluido el gate `tests/test_rest_contract.py`, que construye
su app con `create_app()` y por tanto **no admite inyección**— habla con un
servidor `EventsService` de referencia en proceso, sobre un puerto efímero de
`127.0.0.1`. Sin él, `POST /lives/advance` llamaría a
`events.default.svc:50051` (que no existe en local) y el round-trip
crear → avanzar → decidir se quedaría sin evento.

También se sustituye `domain.service.random` por una ruleta que **nunca**
sale mortal: la probabilidad aleatoria de muerte (FR-005, 0,01 con
granularidad `YEAR`) convertiría el gate en un fallo 1 de cada 100
ejecuciones. Los tests que sí necesitan muerte aleatoria (T066) sustituyen la
ruleta con `monkeypatch.setattr(domain.service, "random", ...)`.
"""

from __future__ import annotations

import os
import random
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterator

# 1) Dirección del microservicio ANTES de cualquier import del proyecto.
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as _probe:
    _probe.bind(("127.0.0.1", 0))
    _FREE_PORT = int(_probe.getsockname()[1])
os.environ["EVENTS_GRPC_ADDRESS"] = f"127.0.0.1:{_FREE_PORT}"

# 2) A partir de aquí ya se puede importar el proyecto.
import grpc
import pytest
from grpc_client._generated import events_pb2, events_pb2_grpc

from domain import service as _domain_service
from domain.service import GameService
from persistence.lives_repo import LivesRepo

#: (tipo, sistema, edad mínima, edad máxima) de la referencia: cubre los 3
#: tipos de FR-003, los 3 sistemas del resumen (FR-020) y deja descartados por
#: edad los que no tocan (FR-013).
_EVENT_SHAPES = (
    (events_pb2.MAIN, "work", 18, 120),
    (events_pb2.DAILY, "education", 0, 120),
    (events_pb2.RELATIONSHIPS, "relationships", 0, 120),
)

#: Dos opciones con `scores` distintos y ninguna mortal: el gate decide con
#: `opt-1` y después avanza otra vez, así que ese evento no puede matar.
#: Los scores son pequeños porque un score de 4 ya son 30 puntos (FR-009).
_OPTIONS = (
    ("opt-1", "Tomártelo con calma", {"happiness": 2, "health": -1}),
    ("opt-2", "Ignorarlo y seguir con el día", {"happiness": -2, "intelligence": 1}),
)

#: Tamaño del catálogo de referencia. Con granularidad `YEAR` hace falta
#: material para 20 avances seguidos (SC-005) empezando a la edad 0, donde
#: sólo `DAILY` y `RELATIONSHIPS` entran por edad.
_CATALOG_SIZE = 100


def _build_catalog() -> list[events_pb2.LifeEvent]:
    catalog: list[events_pb2.LifeEvent] = []
    for index in range(_CATALOG_SIZE):
        event_type, system, min_age, max_age = _EVENT_SHAPES[
            index % len(_EVENT_SHAPES)
        ]
        options = [
            events_pb2.EventOption(
                id=option_id,
                label=label,
                fatal=False,
                scores=[
                    events_pb2.StatScore(stat=stat, score=score)
                    for stat, score in scores.items()
                ],
            )
            for option_id, label, scores in _OPTIONS
        ]
        catalog.append(
            events_pb2.LifeEvent(
                id=f"ev-{index:03d}",
                type=event_type,
                text=f"Suceso de referencia número {index} de la época.",
                min_age=min_age,
                max_age=max_age,
                source_url="https://example.test/reference",
                system=system,
                options=options,
            )
        )
    return catalog


class ReferenceEventsServicer(events_pb2_grpc.EventsServiceServicer):
    """`events.v2` de referencia: catálogo fijo y registro de peticiones.

    Sólo se guarda lo que el backend **pide** (`limit`, `exclude_ids`, edad),
    que es lo que las pruebas de US2 tienen que verificar (FR-003a, SC-005).
    """

    def __init__(self) -> None:
        self.requests: list[events_pb2.GetEventsRequest] = []
        self.catalog = _build_catalog()

    def GetEvents(
        self,
        request: events_pb2.GetEventsRequest,
        context: object,
    ) -> events_pb2.GetEventsResponse:
        self.requests.append(request)
        excluded = set(request.exclude_ids)
        served = [
            event
            for event in self.catalog
            if event.id not in excluded
            and event.min_age <= request.age_years <= event.max_age
        ][: request.limit]
        # FR-002: cada evento servido pertenece al país pedido. Se devuelve una
        # copia para no mutar el catálogo estático.
        scoped: list[events_pb2.LifeEvent] = []
        for event in served:
            copy = events_pb2.LifeEvent()
            copy.CopyFrom(event)
            copy.country = request.country
            scoped.append(copy)
        return events_pb2.GetEventsResponse(
            events=scoped,
            origin=events_pb2.FETCHED,
            year=request.year,
        )


class _NeverFatalRng:
    """Sustituto de `domain.service.random` que jamás sale mortal.

    Sólo intercepta `random()`, que es la tirada de la muerte aleatoria
    (FR-005); todo lo demás (`choice`, `randint`, …) se delega al módulo real.

    ⚠️ No puede ser una subclase de `random.Random` que redefina `random()`:
    al hacerlo, CPython deja de emparejar `_randbelow_with_getrandbits` y usa
    `_randbelow_without_getrandbits`, que recalcula el valor con `random()`
    hasta que cae por debajo del límite… un `random()` fijo en `1.0` es un
    bucle infinito (medido: la suite se colgaba en `create_life`).
    """

    def random(self) -> float:
        return 1.0

    def __getattr__(self, name: str):
        return getattr(random, name)


#: Nacimiento válido para la suite (FR-001): el catálogo acepta AR/Buenos Aires.
BIRTH: dict[str, object] = {
    "gender": "MALE",
    "country": "AR",
    "city": "Buenos Aires",
    "birth_year": 1990,
}


_REFERENCE = ReferenceEventsServicer()
_SERVER = grpc.server(ThreadPoolExecutor(max_workers=4))
events_pb2_grpc.add_EventsServiceServicer_to_server(_REFERENCE, _SERVER)
_SERVER.add_insecure_port(os.environ["EVENTS_GRPC_ADDRESS"])
_SERVER.start()

# 3) Regla del juego fuera del camino de la suite (ver doc del módulo).
_domain_service.random = _NeverFatalRng()


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    _SERVER.stop(grace=0).wait()


@pytest.fixture
def events_service() -> Iterator[ReferenceEventsServicer]:
    """El microservicio de referencia, con el contador de peticiones a cero."""
    _REFERENCE.requests.clear()
    yield _REFERENCE
    _REFERENCE.requests.clear()


@pytest.fixture
def service() -> GameService:
    """Servicio real sobre `${PROJECT}-lives`: las pruebas persisten de verdad."""
    return GameService(LivesRepo())


@pytest.fixture
def born(service: GameService) -> Iterator[Callable[..., str]]:
    """Fábrica de vidas que garantiza la limpieza de lo que cree cada test."""
    created: list[str] = []

    def _born(granularity: str = "YEAR") -> str:
        player_id = str(uuid.uuid4())
        service.create_life(player_id=player_id, granularity=granularity, **BIRTH)
        created.append(player_id)
        return player_id

    yield _born
    for player_id in created:
        service.repo.delete_state(player_id)
