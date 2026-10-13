"""Evolución natural del personaje, sin sucesos de por medio (FR-011, FR-019).

La salud es una stat **jugable**: de joven se recupera (+1 punto por año cumplido
hasta `HEALTH_PEAK_YEARS`) y en la vejez decae, perdiendo un punto por año a
partir de ahí y un punto más cada `HEALTH_DECAY_STEP_YEARS` años (40 → −1,
60 → −2, 80 → −3, …). Así un personaje joven puede volver a subir de salud
—también con las opciones positivas de `generic_events.py`— mientras la muerte
por salud a `0` sigue siendo posible en la vejez. La felicidad fluctúa en ±3 cada
período. Se aplica **siempre**, aunque no llegue ningún evento, y es lo que
garantiza que una vida sin edad máxima pueda terminar.

Aquí vive también la **tirada de muerte aleatoria**, porque es la otra cara de lo
mismo: lo que le pasa al cuerpo sin que el jugador decida nada.

Las dos son puras y deterministas: todo el azar sale de la semilla de la vida
(FR-019), nunca del proceso.
"""

from __future__ import annotations

from .dice import PURPOSE_DEATH, PURPOSE_HAPPINESS, Dice
from .models import Life

#: Cuánto fluctúa la felicidad en cada período, en puntos (±).
HAPPINESS_FLUCTUATION = 3

#: Edad a partir de la cual la salud pasa de recuperarse a decaer.
HEALTH_PEAK_YEARS = 40

#: La pérdida anual crece un punto cada 20 años vividos por encima del pico.
HEALTH_DECAY_STEP_YEARS = 20

#: Recuperación anual de salud por debajo de `HEALTH_PEAK_YEARS`.
HEALTH_YEARLY_RECOVERY = 1


def natural_drift(life: Life, months: int) -> dict[str, int]:
    """Deriva natural de este período (FR-011).

    La salud sólo se toma al **cumplir años**, de modo que el riesgo anual es
    idéntico con granularidad `YEAR` y con `MONTH`: una vida mensual sólo nota la
    deriva de salud una sola vez cada doce avances, igual que una anual. Depende
    **sólo de la edad cumplida**, así que ambos modos dan exactamente el mismo
    balance anualizado.

    La felicidad sí fluctúa en todos los períodos, y sale de la semilla de la
    vida (FR-019): el mismo período de la misma vida da siempre la misma deriva.
    """
    age_months = life.age_months + months
    health = 0
    if age_months % 12 == 0:
        age_years = age_months // 12
        if age_years < HEALTH_PEAK_YEARS:
            health = HEALTH_YEARLY_RECOVERY
        else:
            decay = (age_years - HEALTH_PEAK_YEARS) // HEALTH_DECAY_STEP_YEARS
            health = -(1 + decay)
    return {
        "health": health,
        "happiness": Dice(life.seed).between(
            PURPOSE_HAPPINESS,
            life.period_index,
            -HAPPINESS_FLUCTUATION,
            HAPPINESS_FLUCTUATION,
        ),
    }


def random_death_roll(life: Life) -> bool:
    """Tirada de muerte aleatoria del período (FR-005), derivada de la semilla.

    Está en una función propia —y no dentro de `_cause_of_death`— porque las
    pruebas necesitan una ruleta decidida y, con FR-019, el azar global ya no
    existe: éste es el punto de sustitución.

    El sorteo va ligado al **período**, no a cada decisión, que es lo que dice
    FR-005 («1 % por período»): con el lote de tres sucesos, tirar por decisión
    casi triplicaría el riesgo anual.
    """
    return Dice(life.seed).chance(
        PURPOSE_DEATH, life.period_index, life.random_death_probability
    )


__all__ = [
    "HAPPINESS_FLUCTUATION",
    "HEALTH_DECAY_STEP_YEARS",
    "HEALTH_PEAK_YEARS",
    "HEALTH_YEARLY_RECOVERY",
    "natural_drift",
    "random_death_roll",
]
