# Architecture — robotsix-file-hub

This document describes the internal design of robotsix-file-hub, an
LLM-powered file organization hub.  It assumes you have read the
[README](README.md) for the high-level overview.

---

## System Overview

```
┌──────────────┐       ┌───────────────────┐       ┌──────────┐
│   Frontend   │──/api──│   FastAPI Backend  │──────│ Database  │
│  (React 19 + │       │  (Python ≥3.14)   │       │ (SQLite / │
│   Vite 6)    │       │                    │       │ PostgreSQL)│
└──────────────┘       │  • File upload     │       └──────────┘
                       │  • AI enrichment   │       ┌──────────────┐
                       │  • Keyword search  │──────│ Local FS / S3│
                       │  • Vector search   │       │   storage    │
                       └───────────────────┘       └──────────────┘
```

The backend is a single FastAPI process.  It serves both the REST API
(JSON) and raw file downloads (binary).  The frontend is a separate Vite
dev server in development and a set of static assets in production.

---

## Backend — `src/robotsix_file_hub/`

### Application entry point (`main.py`)

- Creates the FastAPI application with a **lifespan** async context
  manager that:
  1. Runs `Base.metadata.create_all` to ensure tables exist (development
     convenience; production runs Alembic migrations explicitly).
  2. Starts the background task worker pool.
  3. On shutdown, stops workers gracefully.
- Registers four route modules: `files`, `search`, `tasks`, `config`.
- Exposes `/health` (database + storage liveness check) and
  `/deploy-spec` (returns `deploy/docker-compose.yml` for central-deploy
  contracts).
- Configures **UTC ISO-8601 logging** to stdout at the level specified
  by `log_level`.

### Configuration (`config.py`)

All settings are loaded from a single JSON file (`config/config.json` by
default, or the path named by `ROBOTSIX_CONFIG_FILE`) via
`robotsix_config.load_config`.  A single `Settings` class owns every
tunable — database URL, storage backend, LLM endpoint, embedding model,
search weights, and log level.  Other modules import `get_settings()` (a
cached singleton) rather than reading the file directly.

### Database layer (`database.py`, `models.py`, `schemas.py`)

| File | Role |
|---|---|
| `database.py` | Creates the async SQLAlchemy **engine** (using `aiosqlite` by default) and an `async_sessionmaker` factory. |
| `models.py` | Defines the `FileRecord` ORM model — columns for `id`, `filename`, `original_name`, `content_type`, `size`, `storage_path`, enrichment fields (`summary`, `category`, `tags`, `llm_model`), and an **embedding vector** (`pgvector`). |
| `schemas.py` | Pydantic models for request/response serialization: `FileUploadResponse`, `FileMetadata`, `FileListResponse`, `SearchRequest`, `SearchResponse`, `TaskStatus`, etc. |

**Key design decision:** the database URL is configurable at runtime.
The default is `sqlite+aiosqlite:///./file_hub.db` (zero-config, single
file).  Swap to PostgreSQL by setting `database_url` to a
`postgresql+asyncpg://…` URL — the SQLAlchemy ORM and Alembic migrations
work identically against both.

### Storage abstraction (`storage.py`)

File storage is behind an abstract `StorageBackend` interface with three
methods: `save(file_id, content)`, `get(path)`, and `delete(path)`.

Two implementations ship:

| Class | Config flag | Behaviour |
|---|---|---|
| `LocalStorageBackend` | `storage_backend="local"` (default) | Saves files under `local_storage_path` (default `./uploads`). Paths are stable and directly readable. |
| `S3StorageBackend` | `storage_backend="s3"` | Stores objects in an S3-compatible bucket (AWS S3, MinIO, etc.) via boto3. Configuration: endpoint, bucket name, access key, secret key, region. |

The backend is selected once at startup by `create_storage_backend()` and
injected into route handlers via FastAPI dependency injection.  Route
code never imports `boto3` directly — it only talks to the
`StorageBackend` ABC.

### AI enrichment pipeline (`enrichment.py`)

When a file is uploaded, it is queued for **asynchronous enrichment**:

1. **Text extraction** — `extract_text(content, content_type)` extracts
   raw text from the file bytes based on its MIME type:
   - PDF → `pypdf`
   - DOCX → `python-docx`
   - XLSX → `openpyxl`
   - Plain text / Markdown / code → direct decode
   - Unsupported types → skipped (enrichment fields left null)

2. **LLM enrichment** — the extracted text is sent to an
   OpenAI-compatible chat-completions endpoint (defaults to Ollama at
   `http://localhost:11434/v1`, model `llama3.1`).  The LLM is prompted
   to return a JSON object with `summary`, `category`, and `tags`.

3. **Retry** — LLM calls use `robotsix_http.RetryConfig` (3 retries
   with exponential backoff).  Enrichment is best-effort; a failure
   leaves the record's enrichment fields null rather than failing the
   upload.

### Embedding generation (`embeddings.py`)

After enrichment, `build_embedding_text()` concatenates the filename,
summary, tags, and category into a single text string.  This text is
sent to the OpenAI-compatible embeddings endpoint (same API base as
enrichment, configurable via `enrichment_llm_embedding_model`
which falls back to `enrichment_llm_model`).

Previously, embeddings were generated in-process with
`sentence-transformers/all-MiniLM-L6-v2`.  That pulled torch (CUDA
build, 2.7 GB of `nvidia/` wheels plus 689 MB of `triton`) into every
install, despite running only on CPU.  The endpoint was already
configured and OpenAI-compatible, so the local model was removed.

### Hybrid search (`search.py`)

The `/files/search` endpoint performs a **hybrid** keyword + vector
search weighted by `search_vector_weight` (default 0.7):

- **Keyword search** — SQL `LIKE` / `ILIKE` against `filename`,
  `original_name`, `summary`, `tags`, and `category` columns.
- **Vector search** — cosine similarity between the query embedding and
  stored `embedding` vectors, using `pgvector`'s `<=>` operator (or a
  manual cosine-distance computation for SQLite).
- Results are merged, re-ranked by the weighted score, and returned with
  pagination.

### Background tasks (`tasks.py`)

Enrichment and embedding generation run asynchronously in a **worker
pool** (Python `asyncio.Task` pool).  Each uploaded file is enqueued;
workers pick up jobs, call the enrichment and embedding pipelines, and
update the `FileRecord` row with results.  Task status is exposed via
the `/tasks/{task_id}` endpoint for polling.

### API routes (`routes/`)

| Router | Prefix | Endpoints |
|---|---|---|
| `files.py` | — | `POST /files` (upload), `POST /files/batch`, `GET /files` (list), `GET /files/categories`, `GET /files/{id}` (download), `GET /files/{id}/metadata`, `DELETE /files/{id}`, `POST /files/search`, `POST /files/reindex`, `GET /files/reindex/progress` |
| `search.py` | — | `POST /files/search` (hybrid search — logically part of files but in its own module) |
| `tasks.py` | — | `GET /tasks/{task_id}` |
| `config.py` | — | `GET /api/config` (discloses app configuration) |

All routes are registered on the FastAPI app in `main.py` with
`app.include_router()`.  There is no API version prefix.

---

## Frontend — `frontend/`

### Build tooling

- **Vite 6** with `@vitejs/plugin-react` for HMR, bundling, and dev server.
- **TypeScript 5.7** with strict settings.
- **ESLint 9** (flat config) with `typescript-eslint`, `react-hooks`, and
  `react-refresh` plugins.
- **Prettier 3** for formatting.
- **Vitest 3** with `@testing-library/react` and `happy-dom` for testing.

### Dev server proxy

`vite.config.ts` proxies `/api` (stripping the prefix) and `/files` to
`http://localhost:8000`, so the frontend dev server can call the backend
without CORS issues during development.

### Component tree

```
App
├── AppNav (navigation bar)
│   ├── <Link to="/">Home</Link>
│   ├── <Link to="/files">Files</Link>
│   ├── <Link to="/upload">Upload</Link>
│   ├── <Link to="/search">Search</Link>
│   └── NavSearch (inline search form)
└── <Routes>
    ├── "/" → HomePage
    ├── "/files" → FilesPage
    ├── "/files/:fileId" → FileDetailPage
    ├── "/upload" → UploadPage
    └── "/search" → SearchPage
```

### Pages

| Page | Purpose |
|---|---|
| `HomePage` | Landing page with project introduction. |
| `FilesPage` | File browser with pagination, category/tag filters, and delete actions. |
| `FileDetailPage` | Single-file view with metadata display, download, and inline preview (PDF, images, text). |
| `UploadPage` | Drag-and-drop file upload with the `UploadDialog` component. |
| `SearchPage` | Hybrid search interface — text query + results display. |

### Shared modules

| Module | Purpose |
|---|---|
| `api.ts` | Typed API client — `fetch` wrappers for every backend endpoint. |
| `lib/format.ts` | Display helpers: `formatSize` (B/KB/MB), `formatDate` (ISO → locale). |
| `lib/preview.ts` | File preview logic — determines which file types can be previewed inline. |
| `components/UploadDialog.tsx` | Reusable drag-and-drop upload dialog with progress feedback. |
| `components/FilePreview.tsx` | Inline file preview component (PDF via `<object>`, images via `<img>`, text via `<pre>`). |

---

## Database Migrations — `migrations/`

Migrations are managed with **Alembic** in async mode:

- `alembic.ini` — configures the script location (`migrations/`) and
  defaults.  The `sqlalchemy.url` is overridden at runtime by `env.py`
  from the application `Settings`.
- `migrations/env.py` — Alembic environment.  Reads `Settings` for the
  database URL, creates an async engine via `async_engine_from_config`,
  and runs migrations inside `asyncio.run()`.
- `migrations/versions/` — ordered migration scripts:

  | Migration | Purpose |
  |---|---|
  | `0001_initial_create_file_records` | Creates the `file_records` table with all metadata columns. |
  | `0002_pgvector_embedding` | Enables the `pgvector` extension and adds the `embedding` column. |
  | `0003_embedding_dim_1024` | Resizes the embedding vector dimension to 1024. |

Migrations are applied with `alembic upgrade head` before starting the
server in production.  In development, `Base.metadata.create_all` in the
lifespan creates tables automatically.

---

## Deployment

### Docker image (`Dockerfile`)

A multi-stage Python build:

1. **Build stage** — copies `pyproject.toml` and `uv.lock`, runs `uv
   sync` to install dependencies, then copies the `src/` tree.
2. **Runtime stage** — copies the virtual environment and source from
   the build stage.  The entry point is `uvicorn
   robotsix_file_hub.main:app`.

### Docker Compose

| File | Purpose |
|---|---|
| `docker-compose.yml` (repo root) | Local development stack: backend container + PostgreSQL + MinIO (S3-compatible storage). |
| `deploy/docker-compose.yml` | Production deployment spec — served by the `/deploy-spec` endpoint for central-deploy. |

Both compose files use named volumes (`uploads-data`, `db-data`) for
persistent data across container restarts.

---

## Testing

### Backend tests (`tests/`)

**pytest** with `pytest-asyncio` (`asyncio_mode = "auto"`).  All test
modules share fixtures from `tests/conftest.py`:

- `test_client` — `httpx.AsyncClient` wired to the FastAPI app.
- `test_db_session` — isolated SQLAlchemy async session (rolled back
  after each test).
- `test_storage` — temporary directory-backed `LocalStorageBackend`.
- `test_session_factory` — session factory for tests that spawn
  background tasks.

Tests cover: upload, download, delete, health, search (keyword +
vector), enrichment, embeddings, storage, tasks, and config routes.

### Frontend tests (`frontend/src/`)

**Vitest** with `@testing-library/react` and `happy-dom`.  Tests are
co-located with source files (`*.test.ts`, `*.test.tsx`).  Coverage
includes: API client, formatting utilities, upload dialog, file list
page, and preview logic.
