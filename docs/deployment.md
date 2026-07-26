# Deployment Guide

This guide covers production deployment of the robotsix-file-hub backend.
The frontend is a static SPA — build it with `npm run build` and serve the
`frontend/dist/` directory from any static file server or CDN.

---

## Backend

### Running with uvicorn

The simplest production setup uses uvicorn directly:

```bash
uv run uvicorn robotsix_file_hub.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers 4 \
  --log-level info
```

- `--workers 4` — spawns 4 worker processes (adjust to CPU count).
- The lifespan handler creates database tables and starts background
  enrichment workers on startup.

### Reverse proxy (nginx)

Place the backend behind nginx for TLS termination, rate limiting, and
static file serving:

```nginx
server {
    listen 443 ssl;
    server_name file-hub.example.com;

    ssl_certificate     /etc/ssl/certs/file-hub.crt;
    ssl_certificate_key /etc/ssl/private/file-hub.key;

    # Frontend static files
    root /var/www/file-hub/frontend/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }

    # API proxy
    location /api/ {
        proxy_pass http://127.0.0.1:8000/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Increase timeout for large uploads
        proxy_read_timeout 300s;
        client_max_body_size 100m;
    }
}
```

---

## Storage

### Local filesystem (default)

Set `FILE_HUB_STORAGE_BACKEND=local` and `FILE_HUB_LOCAL_STORAGE_PATH` to a
writable directory (e.g. `/data/file-hub/uploads`). Ensure the uvicorn
process has read/write access.

Back up the `uploads/` directory and the SQLite database file together.

### S3-compatible storage

Set `FILE_HUB_STORAGE_BACKEND=s3` and configure the S3 credentials:

```bash
export FILE_HUB_STORAGE_BACKEND=s3
export FILE_HUB_S3_ENDPOINT=https://s3.amazonaws.com     # or MinIO endpoint
export FILE_HUB_S3_BUCKET=file-hub-prod
export FILE_HUB_S3_ACCESS_KEY=AKIAIOSFODNN7EXAMPLE
export FILE_HUB_S3_SECRET_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
export FILE_HUB_S3_REGION=us-east-1
```

The application uses the default AWS credential chain when `S3_ACCESS_KEY`
is empty — set `AWS_PROFILE` or use IAM instance roles instead of explicit
keys.

---

## Database

### SQLite (default)

Zero-config — the database file is created at `FILE_HUB_DATABASE_URL`
(default: `./file_hub.db`). Suitable for single-server deployments.

### PostgreSQL

For multi-worker or high-throughput deployments, switch to PostgreSQL:

```bash
export FILE_HUB_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/file_hub
```

Create the database and run migrations:

```bash
# Create the database
createdb file_hub

# Tables are auto-created on startup by the lifespan handler.
# For managed migrations, use Alembic:
uv run alembic upgrade head
```

---

## LLM / AI Pipeline

The enrichment pipeline requires an OpenAI-compatible API endpoint.

### Local Ollama (default)

```bash
# Install Ollama and pull a model
ollama pull llama3.1

# Start with default config (no env changes needed)
uv run uvicorn robotsix_file_hub.main:app
```

### OpenAI / Azure / other providers

```bash
export FILE_HUB_ENRICHMENT_LLM_API_BASE=https://api.openai.com/v1
export FILE_HUB_ENRICHMENT_LLM_API_KEY=sk-…
export FILE_HUB_ENRICHMENT_LLM_MODEL=gpt-4o-mini
export FILE_HUB_ENRICHMENT_LLM_EMBEDDING_MODEL=text-embedding-3-small
```

---

## Embeddings

The default embedding model (`sentence-transformers/all-MiniLM-L6-v2`) is
downloaded automatically on first use and cached locally.  It runs on CPU
and produces 384-dimensional vectors.

To use a different model:

```bash
export FILE_HUB_EMBEDDING_MODEL_NAME=sentence-transformers/all-mpnet-base-v2
```

Or offload embeddings to the LLM API:

```bash
export FILE_HUB_ENRICHMENT_LLM_EMBEDDING_MODEL=text-embedding-3-small
```

---

## Environment Variables Reference

See [`.env.example`](../.env.example) for a complete annotated list of all
configuration variables with their defaults.
