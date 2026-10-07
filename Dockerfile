FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Dependencias primero (capa cacheable)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Código del backend (incluye los contratos y el generador de stubs)
COPY config.py ./
COPY contracts ./contracts
COPY proto ./proto
COPY tools ./tools
COPY domain ./domain
COPY persistence ./persistence
COPY rest ./rest
COPY grpc_client ./grpc_client

# ⚠️ Los stubs gRPC NO están en git (`.gitignore`) y `grpc_client/events.py`
# los importa en runtime: MUST generarse aquí. Sin este paso la imagen arranca
# y revienta al importar `rest.app` (T084).
RUN bash tools/gen_proto.sh proto/events.proto

EXPOSE 8000

CMD ["uvicorn", "rest.app:app", "--host", "0.0.0.0", "--port", "8000"]
