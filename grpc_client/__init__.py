"""Cliente gRPC `events.v1` del microservicio de eventos.

El backend es el **cliente** y `web-events-service` el **servidor** (research,
Decisión 4): la comunicación es interna, este-oeste, sobre el contrato
versionado `proto/events.proto`, y jamás cruza el ALB.

Los stubs `_generated/` **no** están en git: se regeneran con
`bash tools/gen_proto.sh proto/events.proto` (y en el build de la imagen).
"""

from __future__ import annotations

from .events import EventsClient

__all__ = ["EventsClient"]
