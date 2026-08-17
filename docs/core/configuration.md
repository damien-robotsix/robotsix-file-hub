# Configuration

All settings are read from a single JSON file — `config/config.json` by
default, or the path named by the `ROBOTSIX_CONFIG_FILE` environment
variable. There is no environment overlay: the file is the only source of
config values, and any key it omits falls back to the pydantic default below.

The committed [`config/config.json`](../../config/config.json) is a template
whose defaults match the container layout (persistent volumes at
`/home/app/data` and `/home/app/uploads`). For local development, override
`ROBOTSIX_CONFIG_FILE` with a file that uses relative paths if you want the
database and uploads alongside the repo.

Secrets (`s3_secret_key`, `enrichment_llm_api_key`) are masked on read and
never returned in plain text by the `/config` HTTP surface.

## Logging

| Key | Type | Default | Description |
|---|---|---|---|
| `log_level` | `str` | `INFO` | Application log level — one of `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |

## Database

| Key | Type | Default | Description |
|---|---|---|---|
| `database_url` | `str` | `sqlite+aiosqlite:////home/app/data/file_hub.db` | SQLAlchemy async database URL. Swap to any supported database (PostgreSQL, MySQL, etc.) by changing this value. |

## Storage

| Key | Type | Default | Description |
|---|---|---|---|
| `storage_backend` | `str` | `local` | Storage backend — `"local"` for filesystem, `"s3"` for S3-compatible object storage. |
| `local_storage_path` | `str` | `/home/app/uploads` | Filesystem path for local storage. Only used when `storage_backend=local`. |

## S3 / MinIO

These settings are only used when `storage_backend=s3`.

| Key | Type | Default | Description |
|---|---|---|---|
| `s3_endpoint` | `str` | *(empty)* | S3-compatible endpoint URL (e.g. `http://localhost:9000` for MinIO). |
| `s3_bucket` | `str` | `file-hub` | S3 bucket name. |
| `s3_access_key` | `str` | *(empty)* | S3 access key. |
| `s3_secret_key` | `str` | *(empty)* | S3 secret key. |
| `s3_region` | `str` | `us-east-1` | AWS / S3 region. |

## Upload Limits

| Key | Type | Default | Description |
|---|---|---|---|
| `max_file_size` | `int` | `104857600` (100 MB) | Maximum upload file size in bytes. |

## LLM Enrichment

OpenAI-compatible API for summary, category, and tag generation.

| Key | Type | Default | Description |
|---|---|---|---|
| `enrichment_llm_api_base` | `str` | `http://localhost:11434/v1` | OpenAI-compatible LLM API base URL. |
| `enrichment_llm_api_key` | `str` | *(empty)* | API key for the LLM service (empty = no auth, e.g. local Ollama). |
| `enrichment_llm_model` | `str` | `llama3.1` | LLM model name used for enrichment. |
| `enrichment_llm_timeout` | `float` | `30.0` | HTTP timeout in seconds for LLM API calls. |
| `enrichment_llm_max_tokens` | `int` | `256` | Maximum tokens in the LLM completion response. |
| `enrichment_llm_embedding_model` | `str` | `bge-m3` | Separate embedding model served by the enrichment API. |

## Search

| Key | Type | Default | Description |
|---|---|---|---|
| `search_vector_weight` | `float` | `0.7` | Hybrid search balance — `0.0` = keyword-only, `1.0` = vector-only. |
