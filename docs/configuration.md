# Configuration

All settings are read from environment variables prefixed with `FILE_HUB_`.
See [`.env.example`](../.env.example) for a complete annotated example file.

## Logging

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_LOG_LEVEL` | `str` | `INFO` | Application log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

## Database

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_DATABASE_URL` | `str` | `sqlite+aiosqlite:///./file_hub.db` | SQLAlchemy async database URL. Swap to any supported database (PostgreSQL, MySQL, etc.) by changing this value. |

## Storage

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_STORAGE_BACKEND` | `str` | `local` | Storage backend — `"local"` for filesystem, `"s3"` for S3-compatible object storage. |
| `FILE_HUB_LOCAL_STORAGE_PATH` | `str` | `./uploads` | Filesystem path for local storage. Only used when `FILE_HUB_STORAGE_BACKEND=local`. |

## S3 / MinIO

These settings are only used when `FILE_HUB_STORAGE_BACKEND=s3`.

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_S3_ENDPOINT` | `str` | *(empty)* | S3-compatible endpoint URL (e.g. `http://localhost:9000` for MinIO). |
| `FILE_HUB_S3_BUCKET` | `str` | `file-hub` | S3 bucket name. |
| `FILE_HUB_S3_ACCESS_KEY` | `str` | *(empty)* | S3 access key. |
| `FILE_HUB_S3_SECRET_KEY` | `str` | *(empty)* | S3 secret key. |
| `FILE_HUB_S3_REGION` | `str` | `us-east-1` | AWS / S3 region. |

## Upload Limits

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_MAX_FILE_SIZE` | `int` | `104857600` (100 MB) | Maximum upload file size in bytes. |

## LLM Enrichment

OpenAI-compatible API for summary, category, and tag generation.

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_ENRICHMENT_LLM_API_BASE` | `str` | `http://localhost:11434/v1` | OpenAI-compatible LLM API base URL. |
| `FILE_HUB_ENRICHMENT_LLM_API_KEY` | `str` | *(empty)* | API key for the LLM service (empty = no auth, e.g. local Ollama). |
| `FILE_HUB_ENRICHMENT_LLM_MODEL` | `str` | `llama3.1` | LLM model name used for enrichment. |
| `FILE_HUB_ENRICHMENT_LLM_TIMEOUT` | `float` | `30.0` | HTTP timeout in seconds for LLM API calls. |
| `FILE_HUB_ENRICHMENT_LLM_MAX_TOKENS` | `int` | `256` | Maximum tokens in the LLM completion response. |
| `FILE_HUB_ENRICHMENT_LLM_EMBEDDING_MODEL` | `str` | *(empty)* | Optional separate embedding model. When empty, falls back to the enrichment model. |

## Search

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_SEARCH_VECTOR_WEIGHT` | `float` | `0.7` | Hybrid search balance — `0.0` = keyword-only, `1.0` = vector-only. |

## Embeddings

| Variable | Type | Default | Description |
|---|---|---|---|
| `FILE_HUB_EMBEDDING_MODEL_NAME` | `str` | `sentence-transformers/all-MiniLM-L6-v2` | sentence-transformers model for local embedding generation. Downloaded automatically on first use. |
