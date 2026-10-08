"""Servicio de dominio del juego: la vida completa (T024, T052, T053).

Toda la regla de negocio vive aquí, sin dependencias de framework: el
adaptador REST y las pruebas consumen la misma clase.

Reglas de creación (FR-001, FR-002, FR-010, FR-014):

- `gender`, `country`, `city` y `birth_year` son **obligatorios**;
- `1900 ≤ birth_year ≤ año actual`;
- `granularity` por defecto `YEAR` y **fija** durante toda la vida;
- `age_months` empieza en `0` y las cuatro stats en **50**;
- los padres existen desde la creación, con nombre y estado.

Reglas de avance (FR-002, FR-003, FR-011, Decisión 5):

- cada avance suma **12 meses** (`YEAR`) o **1** (`MONTH`), aplica la
  **deriva natural** y presenta **un solo evento**, que queda pendiente de
  decisión hasta que el jugador elija (el siguiente avance espera);
- la cola `reserve[]` guarda el resto del lote devuelto por el
  microservicio, así que sólo se consulta cuando se vacía, siempre con
  `limit` 2/5 y `exclude_ids` de todo lo servido (SC-005);
- si no llega ningún suceso (microservicio caído o sin nada para esa edad)
  el avance **sigue adelante** con un suceso genérico (edge cases de la
  spec), nunca con un error que bloquee la partida.

Reglas de decisión (FR-005, SC-003):

- los `deltas` elegidos se aplican sobre las 4 stats recortándolas a
  `0 ≤ x ≤ 100` y quedan en el historial (`EVENT#<seq:06d>`);
- la muerte por las 3 vías: opción `fatal`, probabilidad aleatoria
  (`0.01` con `YEAR`, `0.0008` con `MONTH`) y salud a `0`.

Reglas de cierre (FR-005, data-model §2):

- el resumen sólo existe con la vida terminada (`409` mientras corre) y se
  **recalcula** en cada lectura agrupando `event_type` sobre el historial:
  sin estadísticas derivadas guardadas;
- `DELETE /lives` borra la partida para poder empezar otra (SC-004).

La vida se guarda en `${PROJECT}-lives` con `PK = PLAYER#<uuid>` /
`SK = STATE`; una segunda vida del mismo jugador choca con `409` porque en
v1 solo hay una partida por jugador (FR-008).
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from datetime import datetime, timezone

from grpc_client.events import EventsClient, events_pb2

from persistence.lives_repo import LivesRepo

from .catalog import validate_birth
from .generic_events import generic_event
from .models import (
    STAT_MIN,
    CauseOfDeath,
    DomainError,
    EventOption,
    EventSource,
    EventType,
    Gender,
    Granularity,
    Life,
    LifeEvent,
    LifeStatus,
    PersonaCercana,
    PresentedEvent,
    Relation,
)

logger = logging.getLogger(__name__)

#: FR-001 / Assumptions: el año de nacimiento aceptado empieza en 1900.
MIN_BIRTH_YEAR = 1900

#: FR-003a: eventos pedidos por consulta, según la granularidad de la vida.
EVENTS_PER_PERIOD: dict[Granularity, int] = {
    Granularity.YEAR: 5,
    Granularity.MONTH: 2,
}

#: FR-011: deriva natural. La salud baja con la edad —un punto por año
#: cumplido, y uno más cada 20 años— y la felicidad fluctúa en ±3 cada
#: período. Se aplica **siempre**, aunque no llegue ningún evento, y es lo
#: que garantiza que una vida sin edad máxima pueda terminar.
HAPPINESS_FLUCTUATION = 3
HEALTH_DECAY_STEP_YEARS = 20

#: FR-005: el resumen de fin de vida agrupa el historial por `event_type`.
#: `COTIDIANO` queda **fuera** del desglose (T062).
SYSTEM_BY_EVENT_TYPE: dict[EventType, str] = {
    EventType.EDUCATIVO: "education",
    EventType.LABORAL: "work",
    EventType.RELACION: "relationships",
    EventType.SENTIMENTAL: "relationships",
}

#: Los tres sistemas que el contrato exige, presentes aunque estén vacíos.
SYSTEM_NAMES: tuple[str, ...] = ("education", "work", "relationships")

#: `SystemDigest.highlights` tiene `maxItems: 5` en el contrato.
MAX_HIGHLIGHTS = 5


@dataclass(frozen=True)
class AdvanceOutcome:
    """Resultado de `advance_life`: la vida, el evento y la deriva del período."""

    life: Life
    event: PresentedEvent
    derived: dict[str, int]


@dataclass(frozen=True)
class DecisionOutcome:
    """Resultado de `submit_decision`: stats cambiadas y si ha muerto (FR-005)."""

    life: Life
    deltas: dict[str, int]
    dead: bool
    cause_of_death: CauseOfDeath | None


@dataclass(frozen=True)
class SystemDigest:
    """Lo que dejó un sistema en el historial, para `LifeSummary.by_system`."""

    events: int
    decisions: int
    highlights: list[str]


@dataclass(frozen=True)
class LifeSummary:
    """Resumen de fin de vida (FR-005): se recalcula en cada lectura.

    No se guarda ninguna estadística derivada (data-model §2): todo sale de
    agrupar `event_type` sobre las filas `EVENT#<seq>` del historial.
    """

    age_reached: int
    cause_of_death: CauseOfDeath
    by_system: dict[str, SystemDigest]


def natural_drift(life: Life, months: int) -> dict[str, int]:
    """Deriva natural de este período (FR-011).

    La salud sólo se toma al **cumplir años**, de modo que el riesgo anual
    es idéntico con granularidad `YEAR` y con `MONTH`: una vida mensual
    pierde salud una sola vez cada doce avances, igual que una anual.
    La felicidad fluctúa en todos los períodos.
    """
    age_months = life.age_months + months
    health = 0
    if age_months % 12 == 0:
        health = -(1 + (age_months // 12) // HEALTH_DECAY_STEP_YEARS)
    return {
        "health": health,
        "happiness": random.randint(-HAPPINESS_FLUCTUATION, HAPPINESS_FLUCTUATION),
    }


def _from_contract(
    item: events_pb2.LifeEvent, *, year: int, source: EventSource
) -> PresentedEvent | None:
    """Traduce un suceso del contrato `events.v1` al dominio.

    Devuelve `None` si no es jugable (sin enunciado o con un tipo que el
    contrato no define): esos sucesos se descartan en vez de romper el
    avance con una excepción.
    """
    if not item.text.strip():
        return None
    try:
        event_type = EventType(events_pb2.EventType.Name(item.type))
    except (ValueError, AttributeError):  # EVENT_TYPE_UNSPECIFIED u otro ajeno
        return None
    return PresentedEvent(
        id=item.id,
        type=event_type,
        text=item.text,
        options=[
            EventOption(
                id=option.id,
                label=option.label,
                fatal=option.fatal,
                deltas={delta.stat: delta.value for delta in option.deltas},
            )
            for option in item.options
        ],
        year=year,
        source=source,
    )


#: Nombres con los que nacen los padres (FR-014 pide nombre, no identidad).
FATHER_NAMES: tuple[str, ...] = (
    "Antonio",
    "Carlos",
    "Daniel",
    "Diego",
    "Francisco",
    "Javier",
    "Jorge",
    "Luis",
    "Manuel",
    "Miguel",
    "Pedro",
    "Rafael",
)

MOTHER_NAMES: tuple[str, ...] = (
    "Ana",
    "Carmen",
    "Clara",
    "Elena",
    "Isabel",
    "Laura",
    "Lucía",
    "María",
    "Marta",
    "Patricia",
    "Rocío",
    "Sofía",
)


class GameService:
    """Creador, lector y jugador de vidas sobre el repositorio propio."""

    def __init__(
        self,
        repo: LivesRepo | None = None,
        *,
        events: EventsClient | None = None,
    ) -> None:
        self._repo = repo if repo is not None else LivesRepo()
        # Cliente perezoso: crearlo no abre conexión, así que el servicio
        # nace aunque el microservicio esté caído (edge cases de la spec).
        self._events = events

    @property
    def repo(self) -> LivesRepo:
        return self._repo

    @property
    def events(self) -> EventsClient:
        if self._events is None:
            self._events = EventsClient()
        return self._events

    # --- US1 -------------------------------------------------------------

    def create_life(
        self,
        *,
        player_id: str,
        gender: Gender | str,
        country: str,
        city: str,
        birth_year: int,
        granularity: Granularity | str | None = None,
    ) -> Life:
        """Crea la vida y la persiste (FR-001). Lanza `409` si ya existe."""
        life = Life(
            player_id=self._validate_player_id(player_id),
            gender=self._validate_gender(gender),
            country=country,
            city=city,
            birth_year=self._validate_birth_year(birth_year),
            granularity=self._validate_granularity(granularity),
            people=self._new_parents(),
        )
        validate_birth(life.country, life.city)

        # Condicionado: una única partida por jugador en v1 (FR-008) → 409.
        self._repo.put_state(life, create_only=True)
        return life

    def get_life(self, player_id: str) -> Life:
        """Recupera la vida en curso tras recargar o reiniciar (FR-008)."""
        life = self._repo.get_state(player_id)
        if life is None:
            raise DomainError("NOT_FOUND", "no hay vida para este jugador")
        return life

    # --- US2: vivir la vida ------------------------------------------------

    def advance_life(self, *, player_id: str) -> AdvanceOutcome:
        """Avanza un período, aplica la deriva natural y presenta **un** evento.

        `YEAR` suma 12 meses y `MONTH` suma 1 (FR-002). El evento queda en
        `pending_event` hasta que se decida: mientras tanto no se puede
        avanzar otra vez, porque el siguiente avance perdería el suceso.
        """
        life = self.get_life(player_id)
        if not life.is_alive:
            raise DomainError("CONFLICT", "la vida ya terminó")
        if life.pending_event is not None:
            raise DomainError("CONFLICT", "hay un evento sin decidir todavía")

        months = 12 if life.granularity is Granularity.YEAR else 1
        derived = natural_drift(life, months)
        life.stats.apply(derived)
        life.age_months += months

        event = self._next_event(life)
        life.pending_event = event
        if event.id not in life.consumed_ids:
            life.consumed_ids.append(event.id)

        life.touch()
        self._repo.put_state(life)
        return AdvanceOutcome(life=life, event=event, derived=derived)

    def submit_decision(
        self, *, player_id: str, option_id: str
    ) -> DecisionOutcome:
        """Aplica la opción elegida sobre las stats y resuelve el período (FR-005)."""
        life = self.get_life(player_id)
        if not life.is_alive:
            raise DomainError("CONFLICT", "la vida ya terminó")

        event = life.pending_event
        if event is None:
            raise DomainError("CONFLICT", "no hay ningún evento pendiente")

        option = event.option(option_id)  # desconocida → INVALID_ARGUMENT (422)
        deltas = life.stats.apply(option.deltas)  # SC-003: ya recortadas a 0–100

        life.sequence += 1
        record = LifeEvent(
            seq=life.sequence,
            period_index=life.period_index,
            age_months=life.age_months,
            year=life.current_year,
            event_id=event.id,
            event_type=event.type,
            text=event.text,
            option_id=option.id,
            deltas=deltas,
            fatal=option.fatal,
            source=event.source,
        )
        life.pending_event = None

        cause = self._cause_of_death(life, option)
        if cause is not None:
            life.status = LifeStatus.DEAD
            life.cause_of_death = cause

        life.touch()
        # El estado primero: si falla después, el evento ya dejó de estar
        # pendiente y un reintento no vuelve a aplicar los mismos deltas.
        self._repo.put_state(life)
        self._repo.put_event(player_id, record)

        return DecisionOutcome(
            life=life,
            deltas=deltas,
            dead=not life.is_alive,
            cause_of_death=cause,
        )

    # --- US3: cierre de la vida -------------------------------------------

    def get_summary(self, *, player_id: str) -> LifeSummary:
        """Resumen de fin de vida (FR-005), recalculado desde el historial.

        Sólo responde con la vida terminada; mientras sigue en curso el
        contrato pide `409`, la compuerta que evita un resumen a mitad de
        partida. Ninguna estadística se guarda derivada: todo sale de
        agrupar `event_type` sobre las filas `EVENT#<seq>` (data-model §2).
        """
        life = self.get_life(player_id)
        if life.is_alive:
            raise DomainError("CONFLICT", "la vida sigue en curso")

        cause = life.cause_of_death
        if cause is None:
            # Imposible en la práctica: `Life` exige causa al pasar a `DEAD`.
            raise DomainError("CONFLICT", "la vida terminó sin causa registrada")

        buckets: dict[str, list[LifeEvent]] = {name: [] for name in SYSTEM_NAMES}
        for record in self._repo.list_events(player_id):
            system = SYSTEM_BY_EVENT_TYPE.get(record.event_type)
            if system is not None:
                buckets[system].append(record)

        return LifeSummary(
            age_reached=life.age_years,
            cause_of_death=cause,
            by_system={
                name: SystemDigest(
                    events=len(rows),
                    decisions=len(rows),
                    # `list_events` viene ordenado por `SK`, así que `reversed`
                    # deja lo más reciente al frente y se corta en 5.
                    highlights=[record.text for record in reversed(rows)][
                        :MAX_HIGHLIGHTS
                    ],
                )
                for name, rows in buckets.items()
            },
        )

    def delete_life(self, *, player_id: str) -> None:
        """Borra la partida para empezar otra (FR-005, SC-004)."""
        if self._repo.get_state(player_id) is None:
            raise DomainError("NOT_FOUND", "no hay vida para este jugador")
        self._repo.delete_state(player_id)

    # --- US2: obtención de sucesos ---------------------------------------

    def _next_event(self, life: Life) -> PresentedEvent:
        """Saca de `reserve[]`; sólo consulta al microservicio al vaciarse."""
        if life.reserve:
            return life.reserve.pop(0)

        served = self._fetch_events(life)
        if not served:
            # Edge case de la spec: estado controlado, nunca un error que
            # bloquee la partida (ni un evento inexistente que rompa el contrato).
            return generic_event(
                life.consumed_ids,
                year=life.current_year,
                age_years=life.age_years,
                period_index=life.period_index,
            )

        life.reserve = served[1:]
        return served[0]

    def _fetch_events(self, life: Life) -> list[PresentedEvent]:
        """`GetEvents` con el `limit` de la granularidad y lo ya servido."""
        limit = EVENTS_PER_PERIOD[life.granularity]
        excluded = set(life.consumed_ids) | {event.id for event in life.reserve}

        try:
            response = self.events.get_events(
                year=life.current_year,
                age_years=life.age_years,
                limit=limit,
                gender=life.gender.value,
                country=life.country,
                city=life.city,
                exclude_ids=sorted(excluded),
            )
        except Exception:  # noqa: BLE001 - el microservicio puede fallar de todo
            logger.warning(
                "events.v1 no responde para %s; se continúa con sucesos genéricos",
                life.player_id,
                exc_info=True,
            )
            return []

        source = (
            EventSource.SEED
            if response.origin == events_pb2.SEED
            else EventSource.WIKIPEDIA
        )
        served = [
            presented
            for item in response.events
            if (presented := _from_contract(item, year=response.year, source=source))
            is not None
        ]
        # Defensa de SC-005: aunque el servicio llegara a repetir, la vida no.
        return [event for event in served if event.id not in excluded]

    @staticmethod
    def _cause_of_death(life: Life, option: EventOption) -> CauseOfDeath | None:
        """Las 3 vías de FR-005, de la más cierta a la más azarosa."""
        if option.fatal:
            return CauseOfDeath.FATAL_OPTION
        if life.stats.health <= STAT_MIN:
            return CauseOfDeath.HEALTH_ZERO
        # `≤` como en la transición del data-model (§1 «Transiciones de estado»).
        if random.random() <= life.random_death_probability:
            return CauseOfDeath.FATAL_RANDOM
        return None

    # --- validaciones ----------------------------------------------------

    @staticmethod
    def _validate_player_id(player_id: str) -> str:
        if not player_id or not str(player_id).strip():
            raise DomainError("INVALID_ARGUMENT", "player_id es obligatorio")
        return str(player_id)

    @staticmethod
    def _validate_gender(gender: Gender | str) -> Gender:
        if gender is None or (isinstance(gender, str) and not gender.strip()):
            raise DomainError("INVALID_ARGUMENT", "gender es obligatorio")
        try:
            return Gender(gender)
        except ValueError as exc:
            raise DomainError(
                "INVALID_ARGUMENT", "gender debe ser MALE o FEMALE"
            ) from exc

    @staticmethod
    def _validate_granularity(
        granularity: Granularity | str | None,
    ) -> Granularity:
        """FR-002: falta el dato → `YEAR`; un valor desconocido → 422."""
        if granularity is None or granularity == "":
            return Granularity.YEAR
        try:
            return Granularity(granularity)
        except ValueError as exc:
            raise DomainError(
                "INVALID_ARGUMENT", "granularity debe ser YEAR o MONTH"
            ) from exc

    @staticmethod
    def _validate_birth_year(birth_year: int) -> int:
        """`1900 ≤ birth_year ≤ año actual` (spec, Assumptions)."""
        if birth_year is None or birth_year == "":
            raise DomainError("INVALID_ARGUMENT", "birth_year es obligatorio")
        if isinstance(birth_year, bool) or not isinstance(birth_year, int):
            raise DomainError("INVALID_ARGUMENT", "birth_year debe ser un entero")

        current_year = datetime.now(timezone.utc).year
        if birth_year < MIN_BIRTH_YEAR or birth_year > current_year:
            raise DomainError(
                "INVALID_ARGUMENT",
                f"birth_year debe estar entre {MIN_BIRTH_YEAR} y {current_year}",
            )
        return birth_year

    @staticmethod
    def _new_parents() -> list[PersonaCercana]:
        """FR-014: los padres existen desde la creación, ambos vivos."""
        return [
            PersonaCercana(random.choice(FATHER_NAMES), Relation.FATHER),
            PersonaCercana(random.choice(MOTHER_NAMES), Relation.MOTHER),
        ]
