"""Traducción del contrato `events.v2` al dominio (adaptador gRPC de entrada).

Vive en la capa del cliente porque es quien posee el stub generado: el dominio
**no** debe conocer `events_pb2`. Aquí se concentra todo lo que sabe de la forma
del contrato, así que un cambio de contrato toca este fichero y no el dominio.

Es también donde un suceso del contrato se completa con lo que sólo él declara
(`country`, `scope`, `system`, condiciones, `target_relation`) y donde se
**descarta** lo que no es jugable, en vez de romper el avance con una excepción.
"""

from __future__ import annotations

from typing import Any

from domain.events import (
    ConditionKind,
    EventCondition,
    EventOption,
    EventScope,
    EventSource,
    EventType,
    ImpactSkew,
    PeopleEffect,
    PresentedEvent,
)

from ._generated import events_pb2


def _event_type(name: str) -> EventType | None:
    """Tipo del contrato, o `None` si el enum no es uno de los tres del dominio."""
    try:
        return EventType(name)
    except ValueError:  # EVENT_TYPE_UNSPECIFIED u otro ajeno
        return None


def _scope(name: str) -> EventScope:
    try:
        return EventScope(name)
    except ValueError:
        return EventScope.YEAR


def _skew(name: str) -> ImpactSkew:
    """Sesgo de la opción; el contrato lo declara opcional (`UNSPECIFIED`)."""
    try:
        return ImpactSkew(name)
    except ValueError:
        return ImpactSkew.NEUTRAL


def _people_effect(name: str) -> PeopleEffect:
    """Efecto sobre una persona cercana; `UNSPECIFIED` equivale a `ALTER`."""
    try:
        return PeopleEffect(name)
    except ValueError:
        return PeopleEffect.ALTER


def _conditions(items: Any) -> list[EventCondition]:
    """Condiciones de estado del evento (FR-017), saltando las desconocidas."""
    conditions: list[EventCondition] = []
    for item in items:
        kind = None
        try:
            kind = ConditionKind(events_pb2.ConditionKind.Name(item.kind))
        except ValueError:  # CONDITION_KIND_UNSPECIFIED
            pass
        if kind is not None:
            conditions.append(EventCondition(kind=kind, value=item.value))
    return conditions


def to_presented_event(
    item: Any, *, year: int, source: EventSource
) -> PresentedEvent | None:
    """`events.v2.LifeEvent` → `PresentedEvent`, o `None` si no es jugable.

    Se descarta cuando el enunciado viene vacío o el tipo no es uno de los tres
    del dominio: preferimos perder un suceso antes que romper el avance.
    """
    if not item.text.strip():
        return None
    event_type = _event_type(events_pb2.EventType.Name(item.type))
    if event_type is None:
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
                # El contrato envía **scores** (−6…+6), nunca puntos: la curva
                # FR-009 los convierte en el dominio al aplicar la decisión.
                scores={score.stat: score.score for score in option.scores},
                people_effect=_people_effect(
                    events_pb2.PeopleEffect.Name(option.people_effect)
                ),
                impact_skew=_skew(events_pb2.ImpactSkew.Name(option.impact_skew)),
                relationship_score=option.relationship_score,
            )
            for option in item.options
        ],
        year=year,
        source=source,
        country=item.country,
        scope=_scope(events_pb2.EventScope.Name(item.scope)),
        conditions=_conditions(item.conditions),
        target_relation=item.target_relation,
        system=item.system,
    )


def origin_is_seed(response: Any) -> bool:
    """`True` si el servicio sirvió el catálogo semilla (edge cases de la spec)."""
    return response.origin == events_pb2.SEED


__all__ = ["origin_is_seed", "to_presented_event"]
