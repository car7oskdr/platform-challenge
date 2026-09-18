############################
# Etapa 1: dependencias
############################
FROM python:3.14-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev

############################
# Etapa 2: ejecución
############################
FROM python:3.14-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && useradd --create-home --uid 10001 appuser \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv

COPY app/ ./app/
COPY model/ ./model/

COPY migrations/ ./migrations/

USER 10001

# Documenta el puerto; no publica nada por sí solo (eso es el -p del run).
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
