"""Modelo de impacto de una opción: `score` → puntos (FR-009, decisión D2).

La curva es fija y la aplica **el dominio del backend**, nunca el microservicio
(el microservicio solo envía scores)::

    0 → 0    ±1 → ±4    ±2 → ±8    ±3 → ±16
    ±4 → ±30 ±5 → ±50   ±6 → ±100

El signo se preserva y el sesgo (`impact_skew`) desplaza el score ±1 antes de
convertir. En v1 el sesgo es **fijo por opción** (FR-009); el azar es
reproducible y vive fuera de este módulo (FR-019).

Módulo **puro**: sin E/S, sin framework y sin vocabulario del dominio; recibe y
devuelve enteros y cadenas, de modo que `events.py` puede apoyarse en él sin
crear un ciclo de importación.
"""

from __future__ import annotations

from typing import Mapping

#: Rango de un score de stat o de relación (FR-008/FR-018).
SCORE_MIN = -6
SCORE_MAX = 6

#: Puntos por magnitud de score, indexado por `abs(score)` (FR-009 / D2). El
#: microservicio no conoce esta tabla: la conversión es responsabilidad del
#: backend.
POINTS_BY_MAGNITUDE: tuple[int, ...] = (0, 4, 8, 16, 30, 50, 100)

#: Desplazamiento que aplica cada sesgo, por valor del enum `ImpactSkew`.
SKEW_SHIFT: dict[str, int] = {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1}


def clamp_score(score: int) -> int:
    """Recorta un score a `−6…+6` (FR-008/FR-018)."""
    return max(SCORE_MIN, min(SCORE_MAX, int(score)))


def shift_for_skew(skew: str) -> int:
    """Desplazamiento del sesgo; un valor desconocido se trata como `NEUTRAL`."""
    return SKEW_SHIFT.get(skew, 0)


def skewed_score(score: int, skew: str) -> int:
    """Aplica el sesgo al score y vuelve a recortar a `−6…+6`."""
    return clamp_score(clamp_score(score) + shift_for_skew(skew))


def points_for_score(score: int) -> int:
    """Puntos que aplica un score, preservando el signo (FR-009)."""
    magnitude = min(abs(int(score)), SCORE_MAX)
    points = POINTS_BY_MAGNITUDE[magnitude]
    return -points if int(score) < 0 else points


def scores_to_points(
    scores: Mapping[str, int], skew: str = "NEUTRAL"
) -> dict[str, int]:
    """Convierte los scores de una opción en los puntos que se aplicarán."""
    return {
        stat: points_for_score(skewed_score(score, skew))
        for stat, score in scores.items()
    }


def score_for_points(points: int) -> int:
    """Inversa aproximada de la curva, **solo para migrar filas v1**.

    Las vidas guardadas antes del rediseño traen `deltas` en puntos; al leerlas
    se traducen al score más cercano de la tabla para que una decisión en curso
    no pierda su efecto. Para los valores de la propia tabla el viaje de ida y
    vuelta es exacto (4→1→4, 30→4→30).
    """
    magnitude = abs(int(points))
    nearest = min(
        range(len(POINTS_BY_MAGNITUDE)),
        key=lambda index: abs(POINTS_BY_MAGNITUDE[index] - magnitude),
    )
    return -nearest if int(points) < 0 else nearest
