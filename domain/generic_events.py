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

Los 5 tipos de FR-003 están representados.
"""

from __future__ import annotations

from collections.abc import Iterable

from .models import EventOption, EventSource, EventType, PresentedEvent

#: `id → tipo → enunciado → opciones`. Los ids son estables, para poder
#: filtrarlos con `consumed_ids` y no repetir el mismo genérico seguido.
_GENERIC_EVENTS: tuple[dict[str, object], ...] = (
    {
        "id": "generic-cotidiano",
        "type": EventType.COTIDIANO,
        "text": "Un imprevisto doméstico tuerce el plan que tenías para el día.",
        "options": (
            ("opt-1", "Afrontarlo con calma", {"happiness": 4, "health": -1}),
            ("opt-2", "Dejarlo para mañana", {"happiness": -3, "intelligence": 1}),
        ),
    },
    {
        "id": "generic-educativo",
        "type": EventType.EDUCATIVO,
        "text": "Se abre en tu barrio un taller formativo al que te puedes apuntar.",
        "options": (
            ("opt-1", "Apuntarte y aprovecharlo", {"intelligence": 6, "happiness": -1}),
            ("opt-2", "Pasarlo por alto", {"happiness": 2, "intelligence": -1}),
        ),
    },
    {
        "id": "generic-laboral",
        "type": EventType.LABORAL,
        "text": "Llega de última hora una oferta de trabajo que no esperabas.",
        "options": (
            ("opt-1", "Presentarte sin dudarlo", {"intelligence": 4, "health": -2}),
            ("opt-2", "Rechazarla y seguir como estás", {"happiness": 3, "intelligence": -1}),
        ),
    },
    {
        "id": "generic-relacion",
        "type": EventType.RELACION,
        "text": (
            "Un amigo de la vida aparece por sorpresa y os ponéis a hablar "
            "de lo de antes."
        ),
        "options": (
            ("opt-1", "Quedar para recuperar el tiempo", {"happiness": 6, "health": -1}),
            ("opt-2", "Dejarlo estar", {"happiness": -4}),
        ),
    },
    {
        "id": "generic-sentimental",
        "type": EventType.SENTIMENTAL,
        "text": "Conoces a alguien con quien la conversación fluye sin esfuerzo.",
        "options": (
            ("opt-1", "Seguir hablando", {"happiness": 7, "looks": 1}),
            ("opt-2", "Cortar la conversación", {"happiness": -3}),
        ),
    },
)


def generic_event(consumed_ids: Iterable[str], *, year: int) -> PresentedEvent:
    """Devuelve un suceso genérico **aún no presentado**.

    Si ya se gastaron todos (vida muy larga con el microservicio caído),
    empieza el ciclo de nuevo: repetir un suceso concreto es preferible a
    dejar el avance sin evento.
    """
    consumed = set(consumed_ids)
    pending = [
        spec
        for spec in _GENERIC_EVENTS
        if str(spec["id"]) not in consumed
    ]
    spec = pending[0] if pending else _GENERIC_EVENTS[0]

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
