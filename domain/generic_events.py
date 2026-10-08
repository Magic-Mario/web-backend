"""Catálogo genérico de última instancia (spec, *edge cases* 103–105).

Cuando no llega ningún suceso de época —el microservicio no responde, no
encuentra sucesos de esa edad o su respuesta viene vacía— el avance continúa
con uno de estos sucesos genéricos. La partida **nunca** se bloquea y
`AdvanceResponse.event` sigue llegando completo, como exige el contrato.

No sustituye al microservicio (FR-003 sigue mandando en el camino normal):
es la red de seguridad que piden los *edge cases* de la spec (*"el sistema
responde con un estado controlado, no con un error silencioso"* y *"el motor
recurre a eventos genéricos"*). Por eso `source = SEED` y por eso sólo lleva
las **2 opciones no mortales** de cada tipo: matar por una red de seguridad
sería justo lo contrario.

Los 5 tipos de FR-003 están representados, y **cada uno declara su rango de
edad** como pide FR-013: un suceso genérico tampoco se presenta fuera de su
edad (a un recién nacido no se le ofrece una oferta de trabajo).

La salud es una **stat jugable**, no un cuentakilómetros de muerte: el
catálogo mezcla opciones que la suben (afrontar el imprevisto con calma,
reservar la tarde para descansar, rechazar sin culpa, quedar a dar un
paseo) y opciones que la bajan (estrés acumulado, noches de estudio,
exceso de trabajo, aislamiento), y las otras tres stats conservan su
mezcla habitual de positivos y negativos.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import EventOption, EventSource, EventType, PresentedEvent

#: `id → tipo → rango de edad → enunciado → opciones`. Los ids son
#: estables, para poder filtrarlos con `consumed_ids` y no repetir el mismo
#: genérico seguido. Los rangos repiten los del microservicio (FR-013).
#:
#: Los `deltas` de salud van **en las dos direcciones** (4 opciones que la
#: suben y 4 que la bajan), para que cuidarse sea una decisión de juego:
#: afrontar con calma, descansar la tarde, rechazar sin culpa o quedar a
#: dar un paseo la recuperan; el estrés acumulado, el estudio a marchas
#: forzadas, el exceso de trabajo y el aislamiento la consumen.
#: `happiness`, `intelligence` y `looks` siguen con su mezcla de signos de
#: siempre.
_GENERIC_EVENTS: tuple[dict[str, object], ...] = (
    {
        "id": "generic-cotidiano",
        "type": EventType.COTIDIANO,
        "min_age": 0,
        "max_age": 120,
        "text": "Un imprevisto doméstico tuerce el plan que tenías para el día.",
        "options": (
            ("opt-1", "Afrontarlo con calma", {"happiness": 4, "health": 1}),
            (
                "opt-2",
                "Dejarlo para mañana",
                {"happiness": -3, "intelligence": 1, "health": -1},
            ),
        ),
    },
    {
        "id": "generic-educativo",
        "type": EventType.EDUCATIVO,
        "min_age": 6,
        "max_age": 30,
        "text": "Se abre en tu barrio un taller formativo al que te puedes apuntar.",
        "options": (
            (
                "opt-1",
                "Apuntarte y aprovecharlo",
                {"intelligence": 6, "happiness": -1, "health": -1, "looks": -1},
            ),
            (
                "opt-2",
                "Pasarlo por alto",
                {"happiness": 2, "intelligence": -1, "health": 1},
            ),
        ),
    },
    {
        "id": "generic-laboral",
        "type": EventType.LABORAL,
        "min_age": 18,
        "max_age": 120,
        "text": "Llega de última hora una oferta de trabajo que no esperabas.",
        "options": (
            ("opt-1", "Presentarte sin dudarlo", {"intelligence": 4, "health": -2}),
            (
                "opt-2",
                "Rechazarla y seguir como estás",
                {"happiness": 3, "intelligence": -1, "health": 1},
            ),
        ),
    },
    {
        "id": "generic-relacion",
        "type": EventType.RELACION,
        "min_age": 0,
        "max_age": 120,
        "text": (
            "Un amigo de la vida aparece por sorpresa y os ponéis a hablar "
            "de lo de antes."
        ),
        "options": (
            # Un paseo y una conversación al aire libre: la mejor fuente
            # positiva de salud del catálogo genérico.
            ("opt-1", "Quedar para recuperar el tiempo", {"happiness": 6, "health": 2}),
            ("opt-2", "Dejarlo estar", {"happiness": -4, "health": -1}),
        ),
    },
    {
        "id": "generic-sentimental",
        "type": EventType.SENTIMENTAL,
        "min_age": 14,
        "max_age": 120,
        "text": "Conoces a alguien con quien la conversación fluye sin esfuerzo.",
        "options": (
            ("opt-1", "Seguir hablando", {"happiness": 7, "looks": 1}),
            ("opt-2", "Cortar la conversación", {"happiness": -3}),
        ),
    },
)


def generic_event(
    consumed_ids: Iterable[str], *, year: int, age_years: int, period_index: int
) -> PresentedEvent:
    """Devuelve un suceso genérico **aún no presentado** y de su edad (FR-013).

    Se elige entre los que caben en el rango de edad del personaje y, de
    ellos, primero uno que todavía no se haya presentado. Cuando ya se
    gastaron todos —vida muy larga con el microservicio caído— el ciclo se
    gira con el número de período, para no clavarse siempre en el mismo.

    Si a esa edad no cabe **ningún** rango (un personaje de más de 120
    años, más allá del catálogo real) se amplía a los cinco: repetir un
    suceso es preferible a dejar el avance sin evento, que es justo lo que
    piden los *edge cases* 103–105.
    """
    consumed = set(consumed_ids)

    def within_range(spec: dict[str, object]) -> bool:
        return int(spec["min_age"]) <= age_years <= int(spec["max_age"])

    pool = [spec for spec in _GENERIC_EVENTS if within_range(spec)]
    if not pool:
        pool = list(_GENERIC_EVENTS)

    fresh = [spec for spec in pool if str(spec["id"]) not in consumed]
    spec = fresh[0] if fresh else pool[(period_index - 1) % len(pool)]

    return PresentedEvent(
        id=str(spec["id"]),
        type=spec["type"],  # type: ignore[arg-type]
        text=str(spec["text"]),
        options=[
            EventOption(id=option_id, label=label, deltas=dict(deltas))
            for option_id, label, deltas in spec["options"]  # type: ignore[union-attr]
        ],
        year=year,
        source=EventSource.SEED,
    )
