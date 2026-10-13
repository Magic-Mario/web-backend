"""Azar reproducible: un `seed` por vida y un stream por propósito (FR-019, D6).

El dominio **no** usa el azar del proceso. Cada vida guarda su `seed` y cada
sorteo es `H(seed ‖ propósito ‖ período)` — SHA-256 — de modo que:

- recalcular un sorteo da **el mismo** valor: reintentar una petición o recargar
  la página no vuelve a tirar. Hoy la tirada de muerte se hace **antes** de
  guardar el estado, así que un reintento podía matar a un personaje que había
  sobrevivido (research Decisión 17);
- misma semilla + mismas decisiones ⇒ **misma vida**: una partida se puede
  reproducir y compartir;
- **añadir** un propósito nuevo no altera los existentes, porque el nombre del
  propósito entra en el hash: cada uno es un stream independiente. Un
  `random.Random(seed)` secuencial sí los desplazaría todos.

Los propósitos son una **lista cerrada** (research Decisión 17): el nacimiento,
la fluctuación de la felicidad, la muerte y los nombres. El microservicio de
eventos no consume azar.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

#: Propósitos de la lista cerrada. El nombre entra en el hash, así que dos
#: propósitos distintos nunca comparten stream.
PURPOSE_HAPPINESS = "drift.happiness"
PURPOSE_DEATH = "death"
PURPOSE_FATHER_NAME = "names.father"
PURPOSE_MOTHER_NAME = "names.mother"

#: Escala de comparación de `chance`: 53 bits es el entero más grande que un
#: `float` representa sin perder precisión, así que la probabilidad de la spec
#: (1 % anual, 0.08 % mensual) se compara exactamente y da siempre lo mismo.
_SCALE = 1 << 53


def _draws(seed: int, purpose: str, period: int) -> int:
    """`H(seed ‖ propósito ‖ período)` como entero sin signo de 64 bits."""
    payload = f"{seed}|{purpose}|{period}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


@dataclass(frozen=True)
class Dice:
    """El azar de **una** vida. Sin estado: todo sale de `seed`."""

    seed: int

    def between(self, purpose: str, period: int, low: int, high: int) -> int:
        """Entero uniforme en `[low, high]`, ambos incluidos."""
        if high < low:
            raise ValueError(f"rango invertido: {low} > {high}")
        return low + _draws(self.seed, purpose, period) % (high - low + 1)

    def chance(self, purpose: str, period: int, probability: float) -> bool:
        """`True` con la probabilidad dada (0.0 ⇒ nunca, 1.0 ⇒ siempre)."""
        if probability <= 0:
            return False
        if probability >= 1:
            return True
        return _draws(self.seed, purpose, period) % _SCALE < int(probability * _SCALE)

    def pick(self, purpose: str, options: Sequence[str], period: int = 0) -> str:
        """Un elemento de `options`, estable para la misma semilla y período."""
        if not options:
            raise ValueError("no hay opciones que sortear")
        return options[_draws(self.seed, purpose, period) % len(options)]


def new_seed() -> int:
    """Semilla nueva para una vida recién creada.

    Es el **único** punto con entropía del juego: a partir de aquí todo se
    deriva, así que una vida es reproducible desde que nace.
    """
    import secrets

    return secrets.randbits(63)


def seed_for(player_id: str, birth_year: int) -> int:
    """Semilla derivada de la identidad de la vida, para filas sin `seed`.

    Las vidas guardadas antes de FR-019 no traen semilla. Derivarla del jugador
    y del año de nacimiento es **estable**: la misma partida obtiene siempre la
    misma, así que la migración no introduce azar nuevo ni pierde el historial.
    """
    payload = f"{player_id}|{birth_year}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") >> 1


__all__ = [
    "PURPOSE_DEATH",
    "PURPOSE_FATHER_NAME",
    "PURPOSE_HAPPINESS",
    "PURPOSE_MOTHER_NAME",
    "Dice",
    "new_seed",
    "seed_for",
]
