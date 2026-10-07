"""Servicio de dominio del juego: crear y recuperar la vida (T024).

Toda la regla de negocio vive aquí, sin dependencias de framework: el
adaptador REST y las pruebas consumen la misma clase.

Reglas de creación (FR-001, FR-002, FR-010, FR-014):

- `gender`, `country`, `city` y `birth_year` son **obligatorios**;
- `1900 ≤ birth_year ≤ año actual`;
- `granularity` por defecto `YEAR` y **fija** durante toda la vida;
- `age_months` empieza en `0` y las cuatro stats en **50**;
- los padres existen desde la creación, con nombre y estado.

La vida se guarda en `${PROJECT}-lives` con `PK = PLAYER#<uuid>` /
`SK = STATE`; una segunda vida del mismo jugador choca con `409` porque en
v1 solo hay una partida por jugador (FR-008).
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from persistence.lives_repo import LivesRepo

from .catalog import validate_birth
from .models import (
    DomainError,
    Gender,
    Granularity,
    Life,
    PersonaCercana,
    Relation,
)

#: FR-001 / Assumptions: el año de nacimiento aceptado empieza en 1900.
MIN_BIRTH_YEAR = 1900

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
    """Creador y lector de vidas sobre el repositorio propio del backend."""

    def __init__(self, repo: LivesRepo | None = None) -> None:
        self._repo = repo if repo is not None else LivesRepo()

    @property
    def repo(self) -> LivesRepo:
        return self._repo

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
