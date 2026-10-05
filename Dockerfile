FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# Dependencias primero (capa cacheable)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Código del backend (incluye grpc_adapter/_generated, declarado pero no servido)
COPY config.py ./
COPY domain ./domain
COPY rest ./rest
COPY grpc_adapter ./grpc_adapter

EXPOSE 8000

CMD ["uvicorn", "rest.app:app", "--host", "0.0.0.0", "--port", "8000"]
