#!/usr/bin/env bash
# Genera los stubs Python de un contrato .proto versionado.
#
# Uso:  bash tools/gen_proto.sh [proto] [salida]
#   proto   por defecto `proto/events.proto` (ruta relativa a la raíz del repo)
#   salida  por defecto `grpc_client/_generated`
#
# El repo es el dueño de sus contratos (Decisión 13 de la 001), así que cada
# repo genera su propio stub desde SU copia del .proto:
#   cloud-project-backend -> stub de CLIENTE de events.v2
#   web-events-service    -> stub de SERVIDOR de events.v2
#
# ⚠️ Corregido 2026-10-06: el `PROTO_DIR` anterior era `$BACKEND_DIR/../proto`,
# restos del layout monorepo `apps/backend/`; con el split de repos hay que
# apuntar a `$REPO_ROOT/proto`. También dejó de estar hardcodeado a finance.v1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PROTO_REL="${1:-proto/events.proto}"
OUT_REL="${2:-grpc_client/_generated}"

# Los argumentos pueden ser relativos a la raíz del repo o absolutos.
case "$PROTO_REL" in
  /*) PROTO_FILE="$PROTO_REL" ;;
  *)  PROTO_FILE="$REPO_ROOT/$PROTO_REL" ;;
esac
case "$OUT_REL" in
  /*) OUT_DIR="$OUT_REL" ;;
  *)  OUT_DIR="$REPO_ROOT/$OUT_REL" ;;
esac

PROTO_DIR="$(cd "$(dirname "$PROTO_FILE")" && pwd)"
BASE="$(basename "$PROTO_FILE" .proto)"

if [ ! -f "$PROTO_FILE" ]; then
  echo "ERROR: no existe el contrato $PROTO_FILE" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"

uv run --project "$REPO_ROOT" python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  "$PROTO_FILE"

# El stub generado importa `<base>_pb2` de forma absoluta; lo hacemos relativo
# para que el paquete (`grpc_client/`, `server/`) sea autocontenido.
if [ "$(uname)" = "Darwin" ]; then
  sed -i '' "s/^import ${BASE}_pb2 as/from . import ${BASE}_pb2 as/" "$OUT_DIR/${BASE}_pb2_grpc.py"
else
  sed -i "s/^import ${BASE}_pb2 as/from . import ${BASE}_pb2 as/" "$OUT_DIR/${BASE}_pb2_grpc.py"
fi

touch "$OUT_DIR/__init__.py"
echo "Stubs de $PROTO_REL generados en $OUT_DIR"
