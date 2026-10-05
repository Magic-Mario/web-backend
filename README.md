# cloud-project-backend

Servicio de finanzas (Python 3.12 + FastAPI + `uv`): expone REST de borde y
declara el servidor gRPC interno (no desplegado en v1). **Source of truth de
los contratos** (`contracts/openapi.yaml`, `proto/finance.proto`, tags
`contracts/vX.Y.Z`).

## Layout

- `domain/` — lógica de finanzas (inmutable, en memoria en v1)
- `rest/` — adaptador FastAPI desplegado (`rest/app.py`)
- `grpc_adapter/` — adaptador `grpc.aio` declarado, NO desplegado (FR-012)
- `contracts/`, `proto/` — contratos canónicos versionados
- `tests/` — dominio + contrato REST + contrato gRPC (`uv run pytest`)
- `tools/gen_proto.sh` — regenera stubs gRPC
- `k8s/` — `deployment.yaml` + `service.yaml` (NodePort 30080); la imagen se
  fija por tag explícito en el deploy, nunca `latest` (FR-019)
- `.github/workflows/contract-check.yaml` — `buf breaking` + diff OpenAPI vs
  tag previo (breaking sin bump mayor = fallo)
- `.github/workflows/deploy.yaml` — buildx → push ECR (`:sha`, `:semver`) →
  `kubectl apply` → smoke + health check cruzado del frontend con reversión
  (puerta SC-007)

## Uso local

```bash
uv sync && uv run pytest
docker build -t backend:local .
```

Ver `docs/LOCAL_DEV.md` en `cloud-project-infra` para el stack completo.
