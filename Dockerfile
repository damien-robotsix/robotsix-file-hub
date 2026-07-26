FROM python:3.14-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install project dependencies (layer cached unless pyproject.toml changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev

# Copy application source
COPY src/ src/

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "src.robotsix_file_hub.main:app", "--host", "0.0.0.0", "--port", "8000"]
