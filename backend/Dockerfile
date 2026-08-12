ARG PYTHON_IMAGE=python:3.14.7-slim@sha256:a7fb1e634c4a578f9e0bd6327f11a3cde11b7a9395f48e24360c0988bcc5c2bc

FROM ${PYTHON_IMAGE} AS builder

ARG UV_VERSION=0.9.8

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/README.md ./backend/
COPY sdk/python/pyproject.toml sdk/python/README.md ./sdk/python/
COPY examples/standalone-app/pyproject.toml examples/standalone-app/README.md ./examples/standalone-app/
COPY backend/src ./backend/src
COPY sdk/python/src ./sdk/python/src
COPY examples/standalone-app/src ./examples/standalone-app/src

RUN uv sync \
    --frozen \
    --no-dev \
    --no-editable \
    --package ai-hub-platform-backend

FROM ${PYTHON_IMAGE} AS runtime

ENV PATH=/workspace/.venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 ai-hub \
    && useradd --create-home --uid 10001 --gid ai-hub ai-hub

WORKDIR /workspace

COPY --from=builder /workspace/.venv /workspace/.venv
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/alembic-projection.ini ./backend/alembic-projection.ini
COPY backend/migrations ./backend/migrations

USER ai-hub

EXPOSE 8000

CMD ["ai-hub-api"]
