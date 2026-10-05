#!/usr/bin/env bash
# Genera los stubs Python del contrato gRPC interno (finance.v1).
# Uso: bash apps/backend/tools/gen_proto.sh
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_DIR="$(cd "$BACKEND_DIR/../proto" && pwd)"
OUT_DIR="$BACKEND_DIR/grpc_adapter/_generated"

mkdir -p "$OUT_DIR"

uv run --project "$BACKEND_DIR" python -m grpc_tools.protoc \
  -I "$PROTO_DIR" \
  --python_out="$OUT_DIR" \
  --grpc_python_out="$OUT_DIR" \
  --pyi_out="$OUT_DIR" \
  "$PROTO_DIR/finance.proto"

# El archivo generado importa `finance_pb2` de forma absoluta; lo hacemos
# relativo para que `grpc_adapter` sea un paquete autocontenido.
if [ "$(uname)" = "Darwin" ]; then
  sed -i '' 's/^import finance_pb2 as finance__pb2/from . import finance_pb2 as finance__pb2/' "$OUT_DIR/finance_pb2_grpc.py"
else
  sed -i 's/^import finance_pb2 as finance__pb2/from . import finance_pb2 as finance__pb2/' "$OUT_DIR/finance_pb2_grpc.py"
fi

touch "$OUT_DIR/__init__.py"
echo "Stubs generados en $OUT_DIR"
