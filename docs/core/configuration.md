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

Secrets (`s3_secret_key`, `api_key` in the `embedding` block) are masked on read and
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
| `local_storage_path` | `str` | `/home/app/uploads` | Filesystem path for local file storage. |

## Upload Limits

| Key | Type | Default | Description |
|---|---|---|---|
| `max_file_size` | `int` | `104857600` (100 MB) | Maximum upload file size in bytes. |

## LLM Enrichment

Uses **robotsix-llmio** (OpenRouter transport) for summary, category, and tag generation.
The LLM tier level selects the model capability (1–4, default 1 for cheap extraction).

| Key | Type | Default | Description |
|---|---|---|---|
| `enrichment_llm_tier_level` | `int` | `1` | Capability tier level for LLM enrichment (1–4). Higher tiers use more capable models. |

### Langfuse Observability

Langfuse traces are activated when credentials are set. Each project gets its own key pair.

| Key | Type | Default | Description |
|---|---|---|---|
| `langfuse.host` | `str` | `https://langfuse.robotsix.net` | Langfuse instance URL. |
| `langfuse.projects.{alias}.public_key` | `str` | `pk-lf-...` | Langfuse public key for the project. |
| `langfuse.projects.{alias}.secret_key` | `str` | `sk-lf-...` | Langfuse secret key for the project. |

The project alias must be `robotsix-file-hub`.

### OpenRouter

| Key | Type | Default | Description |
|---|---|---|---|
| `openrouter.keys.{alias}` | `str` | `sk-or-...` | OpenRouter API key for the alias. |

The alias must be `robotsix-file-hub` (same as the Langfuse project alias).

## Embedding

Dedicated OpenAI-compatible embedding server (shared bge-m3, 1024 dimensions).

| Key | Type | Default | Description |
|---|---|---|---|
| `embedding.provider` | `str` | `openai_compatible` | Embedding provider identifier. |
| `embedding.model` | `str` | `bge-m3` | Embedding model name — must emit 1024-dim vectors matching the pgvector column. |
| `embedding.endpoint` | `str` | `http://localhost:11434/v1` | Base URL of the shared Ollama bge-m3 embedding server. |
| `embedding.dimensions` | `int` | `1024` | Dimensionality of the embedding vector. |
| `embedding.api_key` | `str` | `ollama` | API key for the embedding endpoint. |
| `embedding.timeout` | `float` | `30.0` | Request timeout in seconds for embedding calls. |

## Search

| Key | Type | Default | Description |
|---|---|---|---|
| `search_vector_weight` | `float` | `0.7` | Hybrid search balance — `0.0` = keyword-only, `1.0` = vector-only. |
