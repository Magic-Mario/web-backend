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
from dataclasses import dataclass
from datetime import datetime, timezone

from grpc_client.events import EventsClient
from grpc_client.mapping import origin_is_seed, to_presented_event

from persistence.lives_repo import LivesRepo

from .catalog import validate_birth
from .dice import PURPOSE_FATHER_NAME, PURPOSE_MOTHER_NAME, Dice, new_seed
from .drift import HAPPINESS_FLUCTUATION, natural_drift, random_death_roll
from .errors import DomainError
from .events import EventOption, EventSource, PresentedEvent
from .generic_events import generic_event
from .names import FATHER_NAMES, MOTHER_NAMES
from .models import (
    BIRTH_STAT_MAX,
    BIRTH_STAT_MIN,
    STAT_MIN,
    STAT_NAMES,
    CauseOfDeath,
    Gender,
    Granularity,
    Life,
    LifeEvent,
    LifeStatus,
    PersonaCercana,
    Relation,
    Stats,
)

logger = logging.getLogger(__name__)

#: FR-001 / Assumptions: el año de nacimiento aceptado empieza en 1900.
MIN_BIRTH_YEAR = 1900

#: FR-007: sucesos que forman el lote de un período — un DAILY, un
#: RELATIONSHIPS y, cuando toca, el MAIN del año. El microservicio nunca
#: devuelve más que esto.
BATCH_LIMIT = 3

#: FR-020: el resumen de fin de vida agrupa el historial por el **sistema** que
#: cada suceso declara (`LifeEvent.system`), no por su tipo de evento: con
#: {MAIN, DAILY, RELATIONSHIPS} el tipo ya no distingue educación de trabajo.
#: Los tres sistemas que el contrato exige, presentes aunque estén vacíos.
SYSTEM_NAMES: tuple[str, ...] = ("education", "work", "relationships")

#: `SystemDigest.highlights` tiene `maxItems: 5` en el contrato.
MAX_HIGHLIGHTS = 5


@dataclass(frozen=True)
class AdvanceOutcome:
    """Resultado de `advance_life`: la vida, el lote del período y su deriva."""

    life: Life
    events: list[PresentedEvent]
    derived: dict[str, int]


@dataclass(frozen=True)
class DecisionOutcome:
    """Resultado de `submit_decision`: stats, muerte y estado del período."""

    life: Life
    deltas: dict[str, int]
    dead: bool
    cause_of_death: CauseOfDeath | None
    #: FR-007: `True` cuando la decisión resolvió el último suceso del lote.
    period_complete: bool


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
        seed = new_seed()
        life = Life(
            player_id=self._validate_player_id(player_id),
            gender=self._validate_gender(gender),
            country=country,
            city=city,
            birth_year=self._validate_birth_year(birth_year),
            granularity=self._validate_granularity(granularity),
            # FR-019: la semilla se sortea **una vez** aquí y de ella se derivan
            # el nacimiento, la deriva, la muerte y los nombres.
            seed=seed,
            # FR-010: una tirada independiente por stat dentro del rango. No es
            # un valor fijo para todo el mundo, y queda persistido con la vida.
            stats=Stats(
                **{
                    name: Dice(seed).between(
                        f"birth.{name}", 0, BIRTH_STAT_MIN, BIRTH_STAT_MAX
                    )
                    for name in STAT_NAMES
                }
            ),
            people=self._new_parents(seed),
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
        """Avanza un período, aplica la deriva y abre el lote de sucesos (FR-007).

        `YEAR` suma 12 meses y `MONTH` suma 1 (FR-002). El período queda
        **abierto** hasta resolver todo el lote: mientras quede algún suceso,
        avanzar otra vez es un `409`, porque perdería los pendientes.
        """
        life = self.get_life(player_id)
        if not life.is_alive:
            raise DomainError("CONFLICT", "la vida ya terminó")
        if not life.period_complete:
            raise DomainError("CONFLICT", "el período todavía tiene sucesos")

        months = 12 if life.granularity is Granularity.YEAR else 1
        derived = natural_drift(life, months)
        life.stats.apply(derived)
        life.age_months += months

        life.pending_batch = self._next_batch(life)
        life.batch_cursor = 0

        life.touch()
        self._repo.put_state(life)
        return AdvanceOutcome(
            life=life, events=list(life.pending_batch), derived=derived
        )

    def submit_decision(
        self, *, player_id: str, event_id: str, option_id: str
    ) -> DecisionOutcome:
        """Resuelve el suceso pendiente del lote y avanza el cursor (FR-007)."""
        life = self.get_life(player_id)
        if not life.is_alive:
            raise DomainError("CONFLICT", "la vida ya terminó")

        event = life.pending_event
        if event is None:
            raise DomainError("CONFLICT", "no hay ningún evento pendiente")
        if event_id != event.id:
            # El contrato exige decidir sobre `pending_batch[batch_cursor]`.
            raise DomainError(
                "CONFLICT",
                f"el evento {event_id!r} no es el pendiente ({event.id!r})",
            )

        option = event.option(option_id)  # desconocida → INVALID_ARGUMENT (422)
        # El microservicio envía scores; el dominio los convierte en puntos con
        # la curva FR-009 antes de recortar a 0–100 (SC-003).
        deltas = life.stats.apply(option.points())

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
            option_label=option.label,
            system=event.system,
            deltas=deltas,
            fatal=option.fatal,
            source=event.source,
        )
        # FR-007: el suceso resuelto queda consumido y el cursor pasa al
        # siguiente; el período se cierra cuando no queda ninguno.
        if event.id not in life.consumed_ids:
            life.consumed_ids.append(event.id)
        life.batch_cursor += 1

        cause = self._cause_of_death(life, option)
        if cause is not None:
            life.status = LifeStatus.DEAD
            life.cause_of_death = cause
            # Morir a mitad de lote cierra el período: los sucesos que quedaban
            # ya no se presentan.
            del life.pending_batch[life.batch_cursor :]

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
            period_complete=life.period_complete,
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
            # Un sistema desconocido o vacío no agrupa: el suceso se vivió,
            # pero no pertenece a ninguno de los tres del contrato (FR-020).
            if record.system in buckets:
                buckets[record.system].append(record)

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

    def get_event_history(self, *, player_id: str) -> list[LifeEvent]:
        """Histórico de sucesos ya vividos, en orden cronológico (§2).

        Sólo exige que la vida **exista** (`404` si no, igual que
        `GET /lives`): un personaje vivo devuelve lo decidido hasta ahora y
        uno muerto, el relato completo. Las filas salen de
        `EVENT#<seq:06d>` con `list_events`, ordenadas por `seq` ascendente
        por construcción; una vida sin ningún período resuelto devuelve
        `[]`, no un error.
        """
        self.get_life(player_id)
        return self._repo.list_events(player_id)

    def delete_life(self, *, player_id: str) -> None:
        """Borra la partida para empezar otra (FR-005, SC-004)."""
        if self._repo.get_state(player_id) is None:
            raise DomainError("NOT_FOUND", "no hay vida para este jugador")
        self._repo.delete_state(player_id)

    # --- US2: obtención de sucesos ---------------------------------------

    def _next_batch(self, life: Life) -> list[PresentedEvent]:
        """El lote del período: lo que sirve el microservicio o el genérico.

        Nunca devuelve una lista vacía: el avance **jamás** deja la partida sin
        suceso (edge cases de la spec). La antigua reserva de un solo evento
        desaparece: el lote entero se presenta de una vez y se resuelve en orden
        (FR-007).
        """
        served = self._fetch_events(life)
        if served:
            return served
        # Edge case de la spec: estado controlado, nunca un error que bloquee la
        # partida (ni un evento inexistente que rompa el contrato).
        return [
            generic_event(
                life.consumed_ids,
                year=life.current_year,
                age_years=life.age_years,
                period_index=life.period_index,
                country=life.country,
            )
        ]

    def _fetch_events(self, life: Life) -> list[PresentedEvent]:
        """`GetEvents` del lote del período, sin repetir lo ya servido."""
        excluded = set(life.consumed_ids) | {
            event.id for event in life.pending_batch
        }

        try:
            response = self.events.get_events(
                year=life.current_year,
                age_years=life.age_years,
                limit=BATCH_LIMIT,
                gender=life.gender.value,
                country=life.country,
                city=life.city,
                exclude_ids=sorted(excluded),
            )
        except Exception:  # noqa: BLE001 - el microservicio puede fallar de todo
            logger.warning(
                "events.v2 no responde para %s; se continúa con sucesos genéricos",
                life.player_id,
                exc_info=True,
            )
            return []

        source = EventSource.SEED if origin_is_seed(response) else EventSource.WIKIPEDIA
        served = [
            presented
            for item in response.events
            if (
                presented := to_presented_event(
                    item, year=response.year, source=source
                )
            )
            is not None
        ]
        # FR-002 (T018): el servicio no estampa ya el país del jugador sobre un
        # suceso ajeno, y un suceso genérico llega **sin** país. Aquí se
        # comprueba en vez de confiar: lo que jamás puede presentarse es un
        # suceso de **otro** país, y si llegara se descarta sin abortar la
        # partida (edge cases de la spec).
        foreign = sorted(
            {event.country for event in served if event.country and event.country != life.country}
        )
        if foreign:
            logger.warning(
                "events.v2 devolvió sucesos de %s para una vida en %s; se descartan",
                foreign,
                life.country,
            )
            served = [
                event for event in served if not event.country or event.country == life.country
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
        if random_death_roll(life):
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
    def _new_parents(seed: int) -> list[PersonaCercana]:
        """FR-014: los padres existen desde la creación, ambos vivos.

        Los nombres salen de la semilla (FR-019): la misma vida nace con los
        mismos padres aunque se reconstruya el estado.
        """
        dice = Dice(seed)
        return [
            PersonaCercana(
                dice.pick(PURPOSE_FATHER_NAME, FATHER_NAMES), Relation.FATHER
            ),
            PersonaCercana(
                dice.pick(PURPOSE_MOTHER_NAME, MOTHER_NAMES), Relation.MOTHER
            ),
        ]
