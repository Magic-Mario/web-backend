"""Vocabulario y mensajes de evento del dominio (FR-003…FR-009, FR-017, FR-018).

Separado de `models.py` (entidades de vida) para que cada módulo tenga una sola
responsabilidad. La dependencia va en un único sentido:

    errors  ←  impact  ←  events  ←  models

Espeja el contrato `events.v2` sin acoplarse al stub de gRPC: el adaptador
(`grpc_client`) traduce entre ambos.

Fuente: `specs/003-mecanicas-de-juego/spec.md` (FR-003…FR-009, FR-017, FR-018) y
`specs/003-mecanicas-de-juego/data-model.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from .errors import DomainError
from .impact import scores_to_points, score_for_points

#: Rango válido de un score de stat o de relación (FR-008/FR-018).
SCORE_MIN = -6
SCORE_MAX = 6


class EventType(str, Enum):
    """FR-003: los tres tipos del rediseño; sentimental se funde en RELATIONSHIPS."""

    MAIN = "MAIN"
    DAILY = "DAILY"
    RELATIONSHIPS = "RELATIONSHIPS"


class EventScope(str, Enum):
    """FR-004/FR-005: granularidad de la que salió el material."""

    YEAR = "YEAR"
    DECADE = "DECADE"


class EventSource(str, Enum):
    """FR-003a y edge cases: `SEED` cuando falló la búsqueda externa."""

    WIKIPEDIA = "WIKIPEDIA"
    SEED = "SEED"


class ImpactSkew(str, Enum):
    """FR-009: sesgo determinista de la opción, fijo en v1."""

    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    NEGATIVE = "NEGATIVE"


class PeopleEffect(str, Enum):
    """FR-009/FR-018: efecto de una opción de RELATIONSHIPS sobre la persona objetivo."""

    ADD = "ADD"
    ALTER = "ALTER"
    END = "END"


class ConditionKind(str, Enum):
    """FR-017: predicados de estado que condicionan un evento; todos con AND."""

    GENDER = "GENDER"
    PARENTS_ALIVE = "PARENTS_ALIVE"
    HAS_PARTNER = "HAS_PARTNER"
    CLOSE_RELATION = "CLOSE_RELATION"
    RELATIONSHIP_BELOW = "RELATIONSHIP_BELOW"
    RELATIONSHIP_ABOVE = "RELATIONSHIP_ABOVE"


@dataclass(frozen=True)
class EventCondition:
    """Predicado de estado que un evento exige para ser elegible (FR-017).

    `value` es una cadena porque el contrato lo declara así (`string value = 2`):
    `"MALE"`, `"true"`, `"FATHER"` o un umbral `"40"` según el `kind`.
    """

    kind: ConditionKind
    value: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, "value": self.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventCondition":
        return cls(kind=ConditionKind(data["kind"]), value=str(data.get("value") or ""))


@dataclass(frozen=True)
class EventOption:
    """Opción de decisión de un evento (FR-008/FR-009).

    La opción declara **scores** (−6…+6) por stat, no puntos: el dominio los
    convierte con la curva de `impact.py`. Ni los scores ni el
    `relationship_score` viajan al navegador: se quedan en el servidor dentro
    del evento pendiente. `fatal` sí viaja, porque el contrato lo declara y
    FR-005a exige que la interfaz lo ponga de manifiesto.
    """

    id: str
    label: str
    scores: dict[str, int] = field(default_factory=dict)
    fatal: bool = False
    people_effect: PeopleEffect = PeopleEffect.ALTER
    impact_skew: ImpactSkew = ImpactSkew.NEUTRAL
    relationship_score: int = 0

    def points(self) -> dict[str, int]:
        """Puntos que aplicarían los scores de esta opción (FR-009)."""
        return scores_to_points(self.scores, self.impact_skew.value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "scores": dict(self.scores),
            "fatal": self.fatal,
            "people_effect": self.people_effect.value,
            "impact_skew": self.impact_skew.value,
            "relationship_score": self.relationship_score,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventOption":
        # Las filas escritas antes del rediseño traen `deltas` en puntos; se
        # traducen al score más cercano para no perder una decisión en curso
        # (ver `impact.score_for_points`). Las filas nuevas traen `scores`.
        if data.get("scores") is not None:
            scores = {k: int(v) for k, v in (data.get("scores") or {}).items()}
        else:
            scores = {
                k: score_for_points(int(v))
                for k, v in (data.get("deltas") or {}).items()
            }
        return cls(
            id=data["id"],
            label=data["label"],
            scores=scores,
            fatal=bool(data.get("fatal", False)),
            people_effect=PeopleEffect(
                data.get("people_effect", PeopleEffect.ALTER.value)
            ),
            impact_skew=ImpactSkew(data.get("impact_skew", ImpactSkew.NEUTRAL.value)),
            relationship_score=int(data.get("relationship_score", 0)),
        )


@dataclass(frozen=True)
class PresentedEvent:
    """Evento servido por el microservicio y todavía sin resolver (FR-003).

    Vive dentro del lote pendiente de la vida (devuelto y aún no presentado) o
    como el evento que el jugador está viendo ahora mismo.
    """

    id: str
    type: EventType
    text: str
    options: list[EventOption] = field(default_factory=list)
    year: int | None = None
    source: EventSource = EventSource.SEED
    #: País al que pertenece el suceso: el backend comprueba el alcance (FR-002).
    country: str = ""
    scope: EventScope = EventScope.YEAR
    #: Predicados que el evento exige; el backend los revalida (FR-017).
    conditions: list[EventCondition] = field(default_factory=list)
    #: Persona cercana a la que se refiere, si es un evento de RELATIONSHIPS
    #: (FR-018). Es una cadena porque el contrato lo declara así.
    target_relation: str = ""
    #: Sistema de vida al que pertenece el suceso (FR-020): "education", "work"
    #: o "relationships" agrupan el resumen de fin de vida (002 FR-005). Vacío
    #: significa que no participa en ningún sistema.
    system: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise DomainError("INVALID_ARGUMENT", "el evento necesita un id")
        if not self.text:
            raise DomainError("INVALID_ARGUMENT", "el evento necesita un enunciado")

    def option(self, option_id: str) -> EventOption:
        for option in self.options:
            if option.id == option_id:
                return option
        raise DomainError(
            "INVALID_ARGUMENT", f"la opción {option_id!r} no pertenece a este evento"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type.value,
            "text": self.text,
            "options": [option.to_dict() for option in self.options],
            "year": self.year,
            "source": self.source.value,
            "country": self.country,
            "scope": self.scope.value,
            "conditions": [condition.to_dict() for condition in self.conditions],
            "target_relation": self.target_relation,
            "system": self.system,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PresentedEvent":
        return cls(
            id=data["id"],
            type=EventType(data["type"]),
            text=data["text"],
            options=[EventOption.from_dict(o) for o in (data.get("options") or [])],
            year=data.get("year"),
            source=EventSource(data.get("source", EventSource.SEED.value)),
            country=str(data.get("country") or ""),
            scope=EventScope(data.get("scope", EventScope.YEAR.value)),
            conditions=[
                EventCondition.from_dict(c) for c in (data.get("conditions") or [])
            ],
            target_relation=str(data.get("target_relation") or ""),
            system=str(data.get("system") or ""),
        )
