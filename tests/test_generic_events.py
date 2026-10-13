"""Los 5 sucesos genéricos: la salud sube **y** baja (red de seguridad).

`domain/generic_events.py` es lo que reparte las stats cuando el
microservicio no responde o no tiene nada para esa edad (edge cases
103–105), así que sus `scores` son los que equilibran la salud en esas
partidas: no pueden ser sólo negativos, o la salud sería un
cuentakilómetros de muerte.

También se comprueba que el rediseño no rompe nada de lo que ya cumplían:
los 3 tipos de FR-003, 2 opciones no mortales cada una, rangos de edad
(FR-013), `scores` sólo sobre las 4 stats (SC-003), el país propagado
(FR-002) y el sistema declarado para el resumen (FR-020).
"""

from __future__ import annotations

from domain.events import EventSource, EventType
from domain.generic_events import generic_event
from domain.models import STAT_NAMES

#: (id, tipo, sistema, edad mínima, edad máxima) — los rangos de FR-013 no
#: cambian; el sistema es lo que agrupa el resumen de fin de vida (FR-020).
_CATEGORIES: tuple[tuple[str, EventType, str, int, int], ...] = (
    ("generic-cotidiano", EventType.DAILY, "", 0, 120),
    ("generic-educativo", EventType.DAILY, "education", 6, 30),
    ("generic-laboral", EventType.MAIN, "work", 18, 120),
    ("generic-relacion", EventType.RELATIONSHIPS, "relationships", 0, 120),
    ("generic-sentimental", EventType.RELATIONSHIPS, "relationships", 14, 120),
)

_ALL_IDS = tuple(event_id for event_id, *_ in _CATEGORIES)

#: País de la partida: la red de seguridad tampoco puede perder el alcance.
_COUNTRY = "AR"


def _row(event_id: str) -> tuple[str, EventType, str, int, int]:
    return next(row for row in _CATEGORIES if row[0] == event_id)


def _event(event_id: str, age_years: int | None = None):
    """Saca el suceso `event_id` gastando antes los otros cuatro."""
    consumed = [other for other in _ALL_IDS if other != event_id]
    _id, _type, _system, min_age, _max_age = _row(event_id)
    return generic_event(
        consumed,
        year=2000,
        age_years=min_age if age_years is None else age_years,
        period_index=1,
        country=_COUNTRY,
    )


def _all_options() -> list[tuple[str, dict[str, int]]]:
    """Las 10 opciones del catálogo: (id del suceso, scores)."""
    return [
        (event_id, dict(option.scores))
        for event_id, *_ in _CATEGORIES
        for option in _event(event_id).options
    ]


def test_every_category_keeps_two_non_fatal_options_of_valid_stats() -> None:
    """2 opciones por suceso, ninguna mortal y ningún score fuera de stats."""
    for event_id, event_type, _system, min_age, _max_age in _CATEGORIES:
        event = _event(event_id, age_years=min_age)

        assert event.id == event_id
        assert event.type is event_type
        assert event.source is EventSource.SEED
        assert len(event.options) == 2, f"{event_id} debe seguir con 2 opciones"
        for option in event.options:
            assert option.scores, f"{event_id}/{option.id} no cambia nada (SC-003)"
            assert set(option.scores) <= set(STAT_NAMES)
            assert not option.fatal, "la red de seguridad no puede matar"


def test_every_category_declares_its_scope_country_and_system() -> None:
    """FR-002 / FR-020: la red de seguridad tampoco pierde país ni sistema."""
    for event_id, _type, system, min_age, _max_age in _CATEGORIES:
        event = _event(event_id, age_years=min_age)

        assert event.country == _COUNTRY
        assert event.system == system
        assert event.scope.value in {"YEAR", "DECADE"}


def test_every_category_stays_inside_its_age_range() -> None:
    """FR-013: el genérico tampoco se presenta fuera de su edad."""
    for event_id, _type, _system, min_age, _max_age in _CATEGORIES:
        # A su edad mínima sí sale…
        assert _event(event_id, age_years=min_age).id == event_id
        # …y por debajo no, aunque los otros cuatro estén gastados.
        if min_age > 0:
            below = _event(event_id, age_years=min_age - 1)
            assert below.id != event_id, f"{event_id} servido a los {min_age - 1}"


def test_health_has_sources_that_raise_it_and_sources_that_lower_it() -> None:
    """El objetivo del rediseño: la salud es una stat jugable."""
    health = [scores.get("health", 0) for _event_id, scores in _all_options()]

    raises = [value for value in health if value > 0]
    lowers = [value for value in health if value < 0]

    # «Al menos un par de opciones deben subir salud» y otras tantas bajarla.
    assert len(raises) >= 2, health
    assert len(lowers) >= 2, health
    assert max(raises) > 0 > min(lowers)


def test_the_other_stats_keep_their_mix_of_signs() -> None:
    """`happiness`, `intelligence` y `looks` siguen con positivos y negativos."""
    options = _all_options()

    for stat in ("happiness", "intelligence", "looks"):
        values = [scores.get(stat, 0) for _event_id, scores in options]
        assert any(value > 0 for value in values), f"{stat} sin fuente positiva"
        assert any(value < 0 for value in values), f"{stat} sin fuente negativa"
