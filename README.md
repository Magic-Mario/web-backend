# cloud-project-backend

Backend del **simulador de vida** estilo BitLife (Python 3.12 + FastAPI + `uv`):
expone el REST de borde del navegador y es el **cliente** gRPC del
microservicio de eventos (`web-events-service`).

**Source of truth de los contratos**: `contracts/openapi.yaml` y
`proto/events.proto`. El backend es quien los define y quien los versiona con
tags `contracts/vX.Y.Z`.

Ya no hay rastro de finanzas: la feature 002 reescribió el dominio, el
contrato y las pruebas (FR-009, SC-009: 0 endpoints de finanzas).

## Qué hace y qué no

**Hace:** modelar la vida completa (crearla, avanzar períodos, aplicar
decisiones, matarla y resumirla), persistirla en DynamoDB y traducir el
dominio al contrato REST.

**No hace:** **no sirve los sucesos**. Los sucesos de época los produce y
sirve el microservicio `web-events-service` por gRPC (`events.v1`); este repo
sólo es su **cliente** (`grpc_client/`). Tampoco autentica (identidad anónima
por cabecera) ni toca la tabla de caché de sucesos, que es del microservicio.

## Dominio

Modelos (`domain/models.py`, sin dependencias de framework):

- **`Life`** — una partida por jugador (v1): `gender`, `country`, `city`,
  `birth_year`, `age_months`, `stats`, `people`, `granularity`, `reserve`,
  `pending_event`, `consumed_ids`, `sequence`, `status`, `cause_of_death`.
  - **`stats`**: `health`, `happiness`, `intelligence`, `looks` — las cuatro
    únicas de v1, **nacen en 50** y viven en escala **0–100** (los deltas se
    recortan al aplicarlos, SC-003).
  - **`people`**: personas cercanas con `relation`
    (`FATHER`/`MOTHER`/`SIBLING`/`FRIEND`/`PARTNER`) y `state`; los padres
    existen desde el nacimiento.
  - **`granularity`**: `YEAR` o `MONTH`, por defecto `YEAR` y **inmutable
    durante toda la vida** (FR-002); `age_months` sólo avanza por acción del
    jugador.
  - **`reserve`**: cola de sucesos ya pedidos y aún no presentados;
    **`pending_event`**: el suceso que el jugador está viendo (sobrevive a la
    recarga); **`consumed_ids`**: ids ya servidos, para no repetir.
  - **`status`**: `ALIVE`/`DEAD`; **`cause_of_death`** obligatorio al morir:
    `HEALTH_ZERO`, `FATAL_OPTION` o `FATAL_RANDOM` (sin edad máxima tope).

Reglas de juego (`domain/service.py`):

- **Creación**: datos de nacimiento obligatorios y validados contra el
  catálogo (`domain/catalog.py` + `birth_catalog.json`); segunda vida del
  mismo jugador → `409`.
- **Avance**: suma 12 meses (`YEAR`) o 1 (`MONTH`), aplica deriva natural y
  presenta **un solo suceso** que queda pendiente de decisión; el microservicio
  sólo se consulta cuando `reserve` se vacía.
- **Decisión**: aplica los `deltas` sobre las stats y resuelve la muerte por
  una de las tres vías; cada período resuelto se guarda como historial.
- **Cierre**: el resumen existe sólo con la vida terminada y se recalcula en
  cada lectura agrupando el historial (sin estadísticas derivadas).
- **Sucesos**: si el microservicio no responde o no hay sucesos para esa edad,
  el avance **nunca se bloquea**: cae en `domain/generic_events.py` — 5
  sucesos genéricos con `source=SEED`, uno por cada tipo de FR-003, con su
  `min_age`/`max_age` (FR-013) y **sólo opciones no mortales**.

Persistencia (`persistence/lives_repo.py`), con `PROJECT=life-simulator-mvp`:

- `life-simulator-mvp-lives` — tabla del backend: `PK = PLAYER#<id>`,
  `SK = STATE` (la vida) o `SK = EVENT#<seq:06d>` (historial, orden
  cronológico lexicográfico).
- `life-simulator-mvp-events-cache` — caché de sucesos, de dueño único del
  microservicio; este backend **no** la toca.

## Rutas del contrato

Las 8 `operationId` de `contracts/openapi.yaml`, servidas **en la raíz y bajo
`/api`** (prefijo que publica el ALB):

| Método | Ruta | Propósito | Códigos relevantes |
|---|---|---|---|
| `GET` | `/health` | readiness del adaptador | `200` |
| `GET` | `/catalog/birth` | catálogo acotado de países/ciudades para nacer (FR-001) | `200` |
| `POST` | `/lives` | crea la vida validando el nacimiento; stats en 50 | `201`, `422`, `409` (ya existe) |
| `GET` | `/lives` | retoma la vida tras recargar, con el suceso pendiente si lo hay (SC-006) | `200`, `404` |
| `POST` | `/lives/advance` | avanza un período y presenta un suceso con sus opciones | `200`, `404`, `409` (vida muerta o suceso sin decidir) |
| `POST` | `/lives/decision` | registra la opción, aplica deltas y resuelve la muerte | `200`, `404`, `409` (sin suceso pendiente) |
| `GET` | `/lives/summary` | resumen de fin de vida: edad, causa y desglose | `200`, `404`, `409` (la vida sigue en curso) |
| `DELETE` | `/lives` | borra la partida para empezar otra (SC-004) | `204`, `404` |

**Identidad anónima** (FR-008): las 6 rutas de vida exigen la cabecera
`X-Player-Id` (UUID v4 generado en el navegador; sin cuentas ni cookies);
`GET /health` y `GET /catalog/birth` **no** la exigen. Una cabecera ausente o
mal formada devuelve `422`.

## Layout

- `domain/` — corazón del juego: `models.py` (vida, stats, personas,
  sucesos), `service.py` (reglas de creación/avance/decisión/cierre),
  `catalog.py` + `birth_catalog.json` (catálogo acotado de nacimiento) y
  `generic_events.py` (los 5 sucesos `SEED` de respaldo)
- `rest/` — adaptador FastAPI desplegado (`rest/app.py`), rutas en raíz y
  `/api`
- `persistence/` — `lives_repo.py`, repositorio de la vida en DynamoDB
  (`${PROJECT}-lives`, dueño único: este backend)
- `grpc_client/` — cliente `events.v1` (`events.py`); los stubs
  `_generated/` **no** están en git (ver `tools/gen_proto.sh`)
- `contracts/`, `proto/` — contratos canónicos versionados
  (`openapi.yaml`, `events.proto`)
- `k8s/` — `deployment.yaml` + `service.yaml` (NodePort 30080) +
  `serviceaccount.yaml`; la imagen se fija por tag explícito en el deploy,
  nunca `latest` (FR-019)
- `tests/` — dominio, creación, avance, resumen y contratos REST y gRPC
- `tools/gen_proto.sh` — regenera los stubs gRPC desde el `.proto` propio
- `.github/workflows/contract-check.yaml` — `buf breaking` + diff OpenAPI vs
  tag previo (breaking sin bump mayor = fallo)
- `.github/workflows/deploy.yaml` — buildx → push ECR (`:sha`, `:semver`) →
  `kubectl apply` → smoke + health check cruzado del frontend con reversión

## Uso local

```bash
# Floci debe estar levantado: la vida se persiste en DynamoDB (FR-008)
cd ../cloud-project-infra && docker compose up -d

cd ../cloud-project-backend
uv sync                                    # dependencias (uv gestiona .venv)
bash tools/gen_proto.sh                    # stubs gRPC en grpc_client/_generated
uv run pytest tests/ -q                    # tests
uv run uvicorn rest.app:app --port 8000    # API en http://localhost:8000
```

```bash
curl -s localhost:8000/health        # {"status":"ok"}
curl -s localhost:8000/api/health    # idéntico, por el prefijo del ALB
```

Swagger UI interactivo: <http://localhost:8000/docs>

```bash
docker build -t backend:local .
```

Ver `docs/LOCAL_DEV.md` en `cloud-project-infra` para el stack completo.

## Contratos como fuente de verdad

`contracts/openapi.yaml` (8 `operationId`) y `proto/events.proto` (`events.v1`,
servicio `EventsService`) **mandan sobre el código**: el frontend los
vendoriza, `web-events-service` comparte el mismo `.proto` byte a byte y las
pruebas de contrato (`tests/test_rest_contract.py`, `tests/test_grpc_contract.py`)
los validan en cada ejecución.

Están versionados por tags `contracts/vX.Y.Z`. El contrato de finanzas de la
001 vivía en **`contracts/v1.0.0`**; el del juego lo reemplaza por completo
(reemplazo total = *breaking*), así que pasa a **`contracts/v2.0.0`**. El CI
(`contract-check.yaml`) falla si hay un cambio *breaking* sin ese bump de tag.
