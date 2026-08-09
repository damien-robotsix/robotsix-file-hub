# Stage 1: Build frontend
FROM node:22-slim AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Python dependencies
FROM python:3.14-slim AS builder
# git is required at BUILD time only: robotsix-http is a git dependency
# (pyproject.toml [tool.uv.sources]), and uv shells out to git to fetch it.
# python:*-slim ships without git, so `uv sync` fails with "Git executable not
# found" — which is what broke every Docker publish from 2026-08-01, 35 minutes
# after that dependency was added. It stays out of the runtime stage below,
# where nothing needs it.
# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY src/ ./src/
RUN uv sync --no-dev --frozen

# Stage 3: Runtime
FROM python:3.14-slim AS runtime
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app

# Copy uv binary (needed for uv run)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy Python venv and source
COPY --from=builder /app/.venv /home/app/.venv
COPY pyproject.toml /home/app/
COPY src/ /home/app/src/

# GET /deploy-spec reads deploy/docker-compose.yml relative to WORKDIR. Without
# this copy the endpoint raises FileNotFoundError → 500 in the container, while
# tests keep passing because they run from the repo root.
COPY deploy/ /home/app/deploy/

# Copy frontend build output
COPY --from=frontend-builder /app/dist /home/app/static

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

USER app

CMD ["uv", "run", "uvicorn", "robotsix_file_hub.main:app", "--host", "0.0.0.0", "--port", "8080"]
