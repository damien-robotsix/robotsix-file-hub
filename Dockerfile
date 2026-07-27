# Stage 1: Build frontend
FROM node:22-slim AS frontend-builder
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Build Python dependencies
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
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

# Copy frontend build output
COPY --from=frontend-builder /app/dist /home/app/static

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"

USER app

CMD ["uv", "run", "uvicorn", "robotsix_file_hub.main:app", "--host", "0.0.0.0", "--port", "8080"]
