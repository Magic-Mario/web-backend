# cloud-project-backend

Backend del **juego de simulación de vida** (Python 3.12 + FastAPI + `uv`):
expone el REST de borde del navegador y es el **cliente** gRPC del
microservicio de eventos. **Source of truth de los contratos**
(`contracts/openapi.yaml`, `proto/events.proto`, tags `contracts/vX.Y.Z`).

Ya no hay rastro de finanzas: la feature 002 reescribió el dominio, el
contrato y las pruebas (FR-009, SC-009).

## Layout

- `domain/` — lógica del juego: `models.py` (vida, stats, personas, eventos)
  y `birth_catalog.json` (catálogo acotado de nacimiento, no es una tabla)
- `persistence/` — `lives_repo.py`, repositorio de la vida en DynamoDB
  (`${PROJECT}-lives`, dueño único: este backend)
- `rest/` — adaptador FastAPI desplegado (`rest/app.py`)
- `grpc_client/` — cliente `events.v1` (`events.py`); los stubs
  `_generated/` **no** están en git (ver `tools/gen_proto.sh`)
- `contracts/`, `proto/` — contratos canónicos versionados
- `tests/` — dominio + contrato REST + contrato gRPC (`uv run pytest`)
- `tools/gen_proto.sh` — regenera stubs gRPC
- `k8s/` — `deployment.yaml` + `service.yaml` (NodePort 30080); la imagen se
  fija por tag explícito en el deploy, nunca `latest` (FR-019)
- `.github/workflows/contract-check.yaml` — `buf breaking` + diff OpenAPI vs
  tag previo (breaking sin bump mayor = fallo)
- `.github/workflows/deploy.yaml` — buildx → push ECR (`:sha`, `:semver`) →
  `kubectl apply` → smoke + health check cruzado del frontend con reversión

## Uso local

```bash
# Floci debe estar levantado: la vida se persiste en DynamoDB (FR-008)
cd ../cloud-project-infra && docker compose up -d

uv sync && bash tools/gen_proto.sh proto/events.proto
uv run pytest                                         # tests
uv run uvicorn rest.app:app --port 8000               # API en http://localhost:8000
```

Todas las rutas se sirven en la **raíz** y bajo **`/api`** (prefijo que
publica el ALB). El contrato completo vive en
[`contracts/openapi.yaml`](contracts/openapi.yaml); ahora mismo está en verde
la ruta de readiness:

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/health` | `{"status": "ok"}` — readiness del adaptador |

```bash
curl -s localhost:8000/health
```

Swagger UI interactivo: <http://localhost:8000/docs>

```bash
docker build -t backend:local .
```

Ver `docs/LOCAL_DEV.md` en `cloud-project-infra` para el stack completo.
