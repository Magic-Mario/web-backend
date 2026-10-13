"""Entidades de vida del dominio de simulación (FR-001…FR-014).

Sin dependencias de framework: el mismo dominio lo consumen el adaptador REST
(FastAPI, desplegado), el repositorio de persistencia en DynamoDB y las pruebas
de dominio.

El vocabulario de eventos vive en `events.py` y el error compartido en
`errors.py`; este módulo solo describe la **vida**: nacimiento, stats, personas
cercanas e historial.

Fuentes: `specs/002-simulacion-vida/spec.md` (FR-001…FR-014) y
`specs/003-mecanicas-de-juego/spec.md` (FR-010 nacimiento, FR-014/FR-018
personas cercanas).

Las cuatro stats son las **únicas** de v1 (FR-004): sin dinero, habilidades
ni energía.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .errors import DomainError
from .events import EventSource, EventType, PresentedEvent

# Escala de stats (FR-004) y valor de nacimiento (FR-010).
STAT_MIN = 0
STAT_MAX = 100
INITIAL_STAT = 50

#: Escala de la gauge oculta de relación por persona cercana (FR-018, D5).
RELATIONSHIP_MIN = 0
RELATIONSHIP_MAX = 100
DEFAULT_RELATIONSHIP = 70

#: Nombre de las cuatro stats, en el orden en que se muestran en la UI.
STAT_NAMES: tuple[str, ...] = ("health", "happiness", "intelligence", "looks")


class Gender(str, Enum):
    """FR-001: hombre o mujer."""

    MALE = "MALE"
    FEMALE = "FEMALE"


class Granularity(str, Enum):
    """FR-002: ciclo del período; `YEAR` es el valor por defecto y es fijo."""

    YEAR = "YEAR"
    MONTH = "MONTH"


class LifeStatus(str, Enum):
    """FR-005: `DEAD` no admite `advance` ni `decision`."""

    ALIVE = "ALIVE"
    DEAD = "DEAD"


class CauseOfDeath(str, Enum):
    """FR-005: las tres vías de muerte (sin edad máxima tope)."""

    HEALTH_ZERO = "HEALTH_ZERO"
    FATAL_OPTION = "FATAL_OPTION"
    FATAL_RANDOM = "FATAL_RANDOM"


class Relation(str, Enum):
    """FR-014: tipo de vínculo de una persona cercana."""

    FATHER = "FATHER"
    MOTHER = "MOTHER"
    SIBLING = "SIBLING"
    FRIEND = "FRIEND"
    PARTNER = "PARTNER"


class PersonState(str, Enum):
    """FR-014/FR-018: `TOGETHER`/`SEPARATED` solo tienen sentido para `PARTNER`.

    `ESTRANDED` (FR-018, D5) es el final de un hermano o un amigo: la relación
    se rompe, pero la persona sigue viva y en el perfil.
    """

    ALIVE = "ALIVE"
    DEAD = "DEAD"
    TOGETHER = "TOGETHER"
    SEPARATED = "SEPARATED"
    ESTRANGED = "ESTRANGED"


#: FR-005: probabilidad de muerte aleatoria POR PERÍODO según granularidad.
#: 1% anual y 0,08% mensual → ~11,4% anualizado en ambos modos (mismo riesgo
#: en los dos modos de juego). Con D6 el sorteo es reproducible (FR-019), pero
#: la probabilidad no cambia.
RANDOM_DEATH_PROBABILITY: dict[Granularity, float] = {
    Granularity.YEAR: 0.01,
    Granularity.MONTH: 0.0008,
}


def now_utc_rfc3339() -> str:
    """Marca temporal en RFC 3339 UTC (`created_at`/`updated_at`, `fetched_at`)."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _clamp(value: int) -> int:
    return max(STAT_MIN, min(STAT_MAX, int(value)))


def _clamp_relationship(value: int) -> int:
    return max(RELATIONSHIP_MIN, min(RELATIONSHIP_MAX, int(value)))


@dataclass
class Stats:
    """Las cuatro stats básicas en escala 0–100 (FR-004, FR-010).

    Todas nacen en `INITIAL_STAT` (50) y no pueden salirse de `[0, 100]`
    (SC-003): los deltas de una decisión se recortan al aplicarlos.
    """

    health: int = INITIAL_STAT
    happiness: int = INITIAL_STAT
    intelligence: int = INITIAL_STAT
    looks: int = INITIAL_STAT

    def __post_init__(self) -> None:
        for name in STAT_NAMES:
            setattr(self, name, _clamp(getattr(self, name)))

    def apply(self, deltas: Mapping[str, int]) -> dict[str, int]:
        """Aplica los `deltas` (puntos) de una opción recortando a `0 ≤ x ≤ 100`.

        Devuelve los deltas tal cual se pidieron: es lo que el contrato
        devuelve en `DecisionResponse.deltas` y lo que se registra en el
        historial (`LifeEvent.deltas`).
        """
        applied: dict[str, int] = {}
        for name, delta in deltas.items():
            if name not in STAT_NAMES:
                raise DomainError("INVALID_ARGUMENT", f"stat desconocida: {name}")
            if isinstance(delta, bool) or not isinstance(delta, int):
                raise DomainError(
                    "INVALID_ARGUMENT", f"delta de {name} debe ser un entero"
                )
            setattr(self, name, _clamp(getattr(self, name) + delta))
            applied[name] = delta
        return applied

    def to_dict(self) -> dict[str, int]:
        return {name: getattr(self, name) for name in STAT_NAMES}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "Stats":
        data = data or {}
        return cls(**{name: int(data.get(name, INITIAL_STAT)) for name in STAT_NAMES})


@dataclass
class PersonaCercana:
    """FR-014/FR-018: persona vinculada visible en el perfil.

    Los padres existen desde la creación; hermanos, amigos y pareja llegan
    por eventos de tipo relación. `relationship` es la gauge oculta 0–100
    (FR-018, D5): no viaja en las decisiones, se muestra en el perfil.
    """

    name: str
    relation: Relation
    state: PersonState = PersonState.ALIVE
    relationship: int = DEFAULT_RELATIONSHIP

    def __post_init__(self) -> None:
        if isinstance(self.relation, str):
            self.relation = Relation(self.relation)
        if isinstance(self.state, str):
            self.state = PersonState(self.state)
        if not self.name or not self.name.strip():
            raise DomainError("INVALID_ARGUMENT", "el nombre no puede estar vacío")
        if self.state in (PersonState.TOGETHER, PersonState.SEPARATED):
            if self.relation is not Relation.PARTNER:
                raise DomainError(
                    "INVALID_ARGUMENT",
                    f"TOGETHER/SEPARATED solo aplica a PARTNER, no a {self.relation.value}",
                )
        self.relationship = _clamp_relationship(self.relationship)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relation": self.relation.value,
            "state": self.state.value,
            "relationship": self.relationship,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PersonaCercana":
        return cls(
            name=data["name"],
            relation=Relation(data["relation"]),
            state=PersonState(data["state"]),
            # Filas escritas antes de la gauge: defecto seguro, nunca un error.
            relationship=int(data.get("relationship", DEFAULT_RELATIONSHIP)),
        )


@dataclass
class LifeEvent:
    """Historial de un período resuelto (data-model §2), fila de la tabla
    `${project}-lives` con `SK = EVENT#<seq:06d>`.

    El orden lexicográfico de `SK` es el orden cronológico. El resumen de
    fin de vida se calcula agrupando `event_type` sobre estas filas, sin
    guardar estadísticas derivadas. `deltas` son los **puntos aplicados**, ya
    convertidos desde los scores de la opción por la curva FR-009.
    """

    seq: int
    period_index: int
    age_months: int
    year: int
    event_id: str
    event_type: EventType
    text: str
    option_id: str
    deltas: dict[str, int]
    fatal: bool
    source: EventSource
    #: Etiqueta de la opción elegida, conocida en el momento de decidir.
    #: **Aditivo**: las filas antiguas, escritas antes de este campo, se
    #: leen con `""` (`from_dict`), y `GET /lives/events` sustituye ese
    #: vacío por el propio `option_label`/`option_id`.
    option_label: str = ""
    #: Sistema de vida del suceso (FR-020), copiado del evento servido, que es
    #: lo que agrupa el resumen de fin de vida. `""` = no participa en ninguno.
    #: **Aditivo**: las filas escritas antes de este campo se leen con `""`.
    system: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "period_index": self.period_index,
            "age_months": self.age_months,
            "year": self.year,
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "text": self.text,
            "option_id": self.option_id,
            "option_label": self.option_label,
            "system": self.system,
            "deltas": dict(self.deltas),
            "fatal": self.fatal,
            "source": self.source.value,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LifeEvent":
        return cls(
            seq=int(data["seq"]),
            period_index=int(data["period_index"]),
            age_months=int(data["age_months"]),
            year=int(data["year"]),
            event_id=data["event_id"],
            event_type=EventType(data["event_type"]),
            text=data["text"],
            option_id=data["option_id"],
            # Filas escritas sin la etiqueta: defecto seguro, nunca un error.
            option_label=str(data.get("option_label") or ""),
            system=str(data.get("system") or ""),
            deltas={k: int(v) for k, v in (data.get("deltas") or {}).items()},
            fatal=bool(data.get("fatal", False)),
            source=EventSource(data["source"]),
        )


def _restore_batch(data: Mapping[str, Any]) -> list[PresentedEvent]:
    """Reconstruye el lote del período (FR-007) a partir de una fila guardada.

    Las filas escritas antes del lote (002) traen `reserve[]` y un
    `pending_event` suelto: se reordenan como lote con el cursor a 0, de modo
    que una partida a medias **sobrevive** a esta actualización y sigue
    mostrando el mismo suceso que tenía pendiente.
    """
    batch = [PresentedEvent.from_dict(e) for e in (data.get("pending_batch") or [])]
    if batch:
        return batch
    legacy: list[PresentedEvent] = []
    if data.get("pending_event"):
        legacy.append(PresentedEvent.from_dict(data["pending_event"]))
    legacy.extend(PresentedEvent.from_dict(e) for e in (data.get("reserve") or []))
    return legacy


@dataclass
class Life:
    """Vida en curso (data-model §1). Fila `PK = PLAYER#<uuid>`, `SK = STATE`.

    Identificada por el **ID anónimo** del navegador (FR-008): en v1 hay una
    única vida por jugador. La granularidad es inmutable durante toda la
    vida (FR-002) y `age_months` solo avanza por acción del jugador.
    """

    player_id: str
    gender: Gender
    country: str
    city: str
    birth_year: int
    granularity: Granularity
    age_months: int = 0
    stats: Stats = field(default_factory=Stats)
    status: LifeStatus = LifeStatus.ALIVE
    cause_of_death: CauseOfDeath | None = None
    people: list[PersonaCercana] = field(default_factory=list)
    #: FR-007: los sucesos del período en curso, en orden de presentación.
    pending_batch: list[PresentedEvent] = field(default_factory=list)
    #: Índice del siguiente suceso a resolver; `== len(pending_batch)` cierra el
    #: período. Sustituye a la antigua `reserve[]` de un solo evento.
    batch_cursor: int = 0
    consumed_ids: list[str] = field(default_factory=list)
    sequence: int = 0
    created_at: str = field(default_factory=now_utc_rfc3339)
    updated_at: str = field(default_factory=now_utc_rfc3339)

    def __post_init__(self) -> None:
        if isinstance(self.gender, str):
            self.gender = Gender(self.gender)
        if isinstance(self.granularity, str):
            self.granularity = Granularity(self.granularity)
        if isinstance(self.status, str):
            self.status = LifeStatus(self.status)
        if isinstance(self.stats, Mapping):
            self.stats = Stats.from_dict(self.stats)
        if self.status is LifeStatus.DEAD and self.cause_of_death is None:
            raise DomainError(
                "INVALID_ARGUMENT", "una vida muerta necesita cause_of_death"
            )

    # --- derivados (data-model §1) -------------------------------------

    @property
    def age_years(self) -> int:
        """Edad en años cumplidos: `floor(age_months / 12)`."""
        return self.age_months // 12

    @property
    def current_year(self) -> int:
        """FR-007: el año de nacimiento fija el año de vida en curso."""
        return self.birth_year + self.age_years

    @property
    def period_index(self) -> int:
        """Nº de períodos transcurridos desde el nacimiento (FR-002)."""
        if self.granularity is Granularity.YEAR:
            return self.age_years
        return self.age_months

    @property
    def decade(self) -> int:
        """Década del año en curso, derivada (FR-005), p. ej. 1991 → 1990."""
        return (self.current_year // 10) * 10

    @property
    def random_death_probability(self) -> float:
        """FR-005: 0.01 con granularidad `YEAR`, 0.0008 con `MONTH`."""
        return RANDOM_DEATH_PROBABILITY[self.granularity]

    @property
    def is_alive(self) -> bool:
        return self.status is LifeStatus.ALIVE

    @property
    def pending_event(self) -> PresentedEvent | None:
        """El suceso que el jugador está viendo ahora (data-model §1, FR-007).

        **Derivado** del lote y del cursor: no se guarda aparte, para que no
        haya dos fuentes de verdad que puedan divergir. `None` cuando el
        período ya está resuelto (o recién abierto, antes de avanzar).
        """
        if 0 <= self.batch_cursor < len(self.pending_batch):
            return self.pending_batch[self.batch_cursor]
        return None

    @property
    def period_complete(self) -> bool:
        """FR-007: no queda ningún suceso del período por resolver."""
        return self.batch_cursor >= len(self.pending_batch)

    def touch(self) -> None:
        self.updated_at = now_utc_rfc3339()

    # --- serialización (JSON embebido en el item de DynamoDB) ----------

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "player_id": self.player_id,
            "gender": self.gender.value,
            "country": self.country,
            "city": self.city,
            "birth_year": self.birth_year,
            "granularity": self.granularity.value,
            "age_months": self.age_months,
            "current_year": self.current_year,
            "current_decade": self.decade,
            "stats": self.stats.to_dict(),
            "status": self.status.value,
            "cause_of_death": (
                self.cause_of_death.value if self.cause_of_death else None
            ),
            "random_death_probability": self.random_death_probability,
            "people": [person.to_dict() for person in self.people],
            "pending_batch": [event.to_dict() for event in self.pending_batch],
            "batch_cursor": self.batch_cursor,
            # Derivado, pero se emite: el navegador sólo necesita el suceso en
            # curso (`GET /lives` lo usa para retomar la partida, SC-006).
            "pending_event": (
                self.pending_event.to_dict() if self.pending_event else None
            ),
            "consumed_ids": list(self.consumed_ids),
            "sequence": self.sequence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "Life":
        return cls(
            player_id=data["player_id"],
            gender=Gender(data["gender"]),
            country=data["country"],
            city=data["city"],
            birth_year=int(data["birth_year"]),
            granularity=Granularity(data["granularity"]),
            age_months=int(data.get("age_months", 0)),
            stats=Stats.from_dict(data.get("stats")),
            status=LifeStatus(data.get("status", LifeStatus.ALIVE.value)),
            cause_of_death=(
                CauseOfDeath(data["cause_of_death"])
                if data.get("cause_of_death")
                else None
            ),
            people=[PersonaCercana.from_dict(p) for p in (data.get("people") or [])],
            pending_batch=_restore_batch(data),
            batch_cursor=int(data.get("batch_cursor", 0)),
            consumed_ids=[str(i) for i in (data.get("consumed_ids") or [])],
            sequence=int(data.get("sequence", 0)),
            created_at=data.get("created_at") or now_utc_rfc3339(),
            updated_at=data.get("updated_at") or now_utc_rfc3339(),
        )
