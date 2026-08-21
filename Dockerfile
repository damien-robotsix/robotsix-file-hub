# Stage 1: Build frontend
FROM node:22-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Python dependencies
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder
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
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:db2d5999728c5837e1bf9ba278ee6b05cef1e95e82a20e27b0c915cb4478b9d7 /uv /usr/local/bin/uv
# Build under the runtime's WORKDIR so the venv's absolute paths (script
# shebangs, pyvenv.cfg) stay valid once it is copied into the runtime stage.
WORKDIR /home/app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project
COPY src/ ./src/
RUN uv sync --no-dev --frozen

# Stage 3: Runtime
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime
RUN useradd --create-home --uid 1000 app
WORKDIR /home/app

# Copy Python venv and source.  uv is deliberately NOT copied here: `uv run`
# re-resolves the environment from pyproject.toml at every start, which needs
# git for the robotsix-http git dependency — absent from this stage — so the
# container crash-looped on "Git executable not found".  The venv is already
# complete, so its uvicorn is invoked directly by CMD below.
COPY --from=builder /home/app/.venv /home/app/.venv
COPY pyproject.toml /home/app/
COPY src/ /home/app/src/

# GET /deploy-spec reads deploy/docker-compose.yml relative to WORKDIR. Without
# this copy the endpoint raises FileNotFoundError → 500 in the container, while
# tests keep passing because they run from the repo root.
COPY deploy/ /home/app/deploy/

# Copy frontend build output
COPY --from=frontend-builder /app/dist /home/app/static

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health/live').read()"

USER app

CMD ["/home/app/.venv/bin/uvicorn", "robotsix_file_hub.main:app", "--host", "0.0.0.0", "--port", "8080"]
