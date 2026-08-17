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

Set `storage_backend` to `local` and `local_storage_path` to a writable
directory (e.g. `/data/file-hub/uploads`) in `config/config.json`. Ensure the
uvicorn process has read/write access.

Back up the `uploads/` directory and the SQLite database file together.

### S3-compatible storage

Set `storage_backend` to `s3` and configure the S3 credentials in
`config/config.json`:

```json
{
  "storage_backend": "s3",
  "s3_endpoint": "https://s3.amazonaws.com",
  "s3_bucket": "file-hub-prod",
  "s3_access_key": "AKIAIOSFODNN7EXAMPLE",
  "s3_secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
  "s3_region": "us-east-1"
}
```

The application uses the default AWS credential chain when `s3_access_key`
is empty — set `AWS_PROFILE` or use IAM instance roles instead of explicit
keys.

---

## Database

### SQLite (default)

Zero-config — the database file is created at `database_url`
(default: `/home/app/data/file_hub.db` in the container). Suitable for
single-server deployments.

### PostgreSQL

For multi-worker or high-throughput deployments, switch to PostgreSQL by
setting `database_url` in `config/config.json`:

```json
{
  "database_url": "postgresql+asyncpg://user:pass@host:5432/file_hub"
}
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

# Start with default config (no config changes needed)
uv run uvicorn robotsix_file_hub.main:app
```

### OpenAI / Azure / other providers

Set the enrichment settings in `config/config.json`:

```json
{
  "enrichment_llm_api_base": "https://api.openai.com/v1",
  "enrichment_llm_api_key": "sk-…",
  "enrichment_llm_model": "gpt-4o-mini",
  "enrichment_llm_embedding_model": "text-embedding-3-small"
}
```

---

## Embeddings

Embeddings are generated via the enrichment LLM API — the same
OpenAI-compatible endpoint used for enrichment. The embedding model is set
by `enrichment_llm_embedding_model` (default `bge-m3`), falling back to
`enrichment_llm_model` when empty.

To use a different model, set it in `config/config.json`:

```json
{
  "enrichment_llm_embedding_model": "text-embedding-3-small"
}
```

---

## Configuration Reference

See [`config/config.json`](../../config/config.json) for the complete list of
settings, and [`docs/core/configuration.md`](configuration.md) for descriptions
and defaults.
