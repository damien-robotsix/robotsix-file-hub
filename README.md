# robotsix-file-hub

LLM-powered file organization hub — upload, enrich, search, and download files
with AI-generated metadata and vector-powered hybrid search.

## Architecture

```
┌──────────────┐       ┌───────────────────┐       ┌─────────┐
│   Frontend   │──/api──│   FastAPI Backend  │──────│ SQLite   │
│  (React 19 + │       │  (Python ≥3.14)   │       │ (default) │
│   Vite 6)    │       │                    │       └─────────┘
└──────────────┘       │  • File upload     │
                       │  • AI enrichment   │       ┌──────────────┐
                       │  • Keyword search  │──────│ Local FS / S3│
                       │  • Vector search   │       │   storage    │
                       └───────────────────┘       └──────────────┘
```

- **Backend** — async FastAPI application with SQLAlchemy ORM. Handles file
  uploads, AI-powered enrichment (LLM summary/category/tags + embedding
  generation), hybrid keyword+vector search, and raw file download.
- **Frontend** — React 19 SPA built with Vite 6 and TypeScript. Provides
  file browser, upload dialog, search page, and file detail views with
  inline previews.
- **Database** — SQLite via `aiosqlite` by default (zero-config); swap to
  any SQLAlchemy-supported database by changing `FILE_HUB_DATABASE_URL`.
- **Storage** — local filesystem by default; S3-compatible object storage
  (AWS S3, MinIO, etc.) supported via the `FILE_HUB_STORAGE_BACKEND=s3` flag.
- **AI Pipeline** — calls an OpenAI-compatible API for LLM enrichment
  (defaults to Ollama at `http://localhost:11434/v1`). Generates embeddings
  locally with `sentence-transformers/all-MiniLM-L6-v2` (384-dim).

## Quick Start

### Prerequisites

- Python ≥ 3.14
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js ≥ 18 (for the frontend)
- [Docker](https://docs.docker.com/compose/) (optional, for PostgreSQL +
  MinIO)
- (Optional) [Ollama](https://ollama.com/) or any OpenAI-compatible LLM API
  for file enrichment

### Docker Compose (recommended)

The easiest way to run the backend with PostgreSQL and MinIO (S3-compatible
storage) is via Docker Compose:

```bash
# Clone and enter the project
git clone <repo-url> && cd robotsix-file-hub

# Copy the example environment file (already configured for Docker)
cp .env.example .env

# Start all services (backend, PostgreSQL, MinIO)
docker compose up --build
```

The backend API is now live at `http://localhost:8000`. Visit `/health` for
a liveness check and `/docs` for the interactive OpenAPI docs.

Start the frontend dev server separately:

```bash
cd frontend
npm install
npm run dev
```

The frontend is now accessible at `http://localhost:5173`.

### Manual Setup (without Docker)

```bash
# Clone and install
git clone <repo-url> && cd robotsix-file-hub
uv sync

# Start the server
uv run uvicorn robotsix_file_hub.main:app --reload --host 0.0.0.0 --port 8000
```

The API is now live at `http://localhost:8000`. Visit `/health` for a
liveness check and `/docs` for the interactive OpenAPI docs.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server starts on `http://localhost:5173` and proxies `/api`
and `/files` requests to the backend at `http://localhost:8000`.

## Authentication

The API supports Bearer-token authentication. When `FILE_HUB_AUTH_TOKEN` is set
to a non-empty value, every `/files/*` endpoint requires an
`Authorization: Bearer <token>` header. Requests without a header receive a
`401` response; requests with a wrong token receive a `403`.

When `FILE_HUB_AUTH_TOKEN` is empty (the default in development), authentication
is disabled — all requests pass through unauthenticated.

The frontend stores the token in `localStorage` under the key
`robotsix-file-hub-token` and sends it as an `Authorization: Bearer <token>`
header on every request.

## Configuration

All settings are read from environment variables prefixed with `FILE_HUB_`.
See [`.env.example`](.env.example) for a complete annotated example.

| Variable | Default | Description |
|---|---|---|
| `FILE_HUB_DATABASE_URL` | `sqlite+aiosqlite:///./file_hub.db` | SQLAlchemy async database URL |
| `FILE_HUB_STORAGE_BACKEND` | `local` | `"local"` or `"s3"` |
| `FILE_HUB_LOCAL_STORAGE_PATH` | `./uploads` | Filesystem path for local storage |
| `FILE_HUB_S3_ENDPOINT` | *(empty)* | S3-compatible endpoint URL |
| `FILE_HUB_S3_BUCKET` | `file-hub` | S3 bucket name |
| `FILE_HUB_S3_ACCESS_KEY` | *(empty)* | S3 access key |
| `FILE_HUB_S3_SECRET_KEY` | *(empty)* | S3 secret key |
| `FILE_HUB_S3_REGION` | `us-east-1` | AWS / S3 region |
| `FILE_HUB_AUTH_TOKEN` | *(empty)* | Bearer token for API auth (empty = no auth in dev) |
| `FILE_HUB_MAX_FILE_SIZE` | `104857600` (100 MB) | Upload size limit in bytes |
| `FILE_HUB_ENRICHMENT_LLM_API_BASE` | `http://localhost:11434/v1` | OpenAI-compatible LLM API base URL |
| `FILE_HUB_ENRICHMENT_LLM_API_KEY` | *(empty)* | API key for the LLM service |
| `FILE_HUB_ENRICHMENT_LLM_MODEL` | `llama3.1` | LLM model name |
| `FILE_HUB_ENRICHMENT_LLM_TIMEOUT` | `30.0` | LLM HTTP timeout (seconds) |
| `FILE_HUB_ENRICHMENT_LLM_MAX_TOKENS` | `256` | Max tokens for LLM completions |
| `FILE_HUB_ENRICHMENT_LLM_EMBEDDING_MODEL` | *(empty)* | Separate embedding model name (falls back to enrichment model) |
| `FILE_HUB_SEARCH_VECTOR_WEIGHT` | `0.7` | Hybrid search balance (0=keyword, 1=vector) |
| `FILE_HUB_EMBEDDING_MODEL_NAME` | `sentence-transformers/all-MiniLM-L6-v2` | Local sentence-transformers model |
| `FILE_HUB_LOG_LEVEL` | `INFO` | Application log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`) |

## API Reference

Base URL: `http://localhost:8000`

| Method | Path | Description | Auth |
|---|---|---|---|
| `GET` | `/health` | Health check — returns `{"status":"ok"}` | No |
| `GET` | `/deploy-spec` | Deploy spec for central-deploy — returns `deploy/docker-compose.yml` with contract-version header | No |
| `POST` | `/files` | Upload a single file (`multipart/form-data`, field `file`) | If configured |
| `POST` | `/files/batch` | Upload multiple files (field `files`) | If configured |
| `GET` | `/files` | List files with pagination and filters (`?category=`, `?tag=`, `?offset=`, `?limit=`, etc.) | If configured |
| `GET` | `/files/categories` | Return distinct, sorted categories across all files | If configured |
| `GET` | `/files/{file_id}` | Download raw file bytes | If configured |
| `GET` | `/files/{file_id}/metadata` | Get file metadata (category, tags, summary, etc.) | If configured |
| `DELETE` | `/files/{file_id}` | Delete a file and its stored data | If configured |
| `POST` | `/files/search` | Hybrid NL search — JSON body `{"query":"…","offset":0,"limit":50}` | If configured |
| `POST` | `/files/reindex` | Re-enqueue enrichment for existing files | If configured |
| `GET` | `/files/reindex/progress` | Reindex progress (`total`, `completed`, `failed`, `active`, `task_id`) | If configured |
| `GET` | `/tasks/{task_id}` | Poll enrichment/reindex task status (`type`, `status`, `progress`, `error`) | If configured |

Full request/response schemas are available in the interactive docs at
`/docs` (Swagger UI) and in [`docs/API.md`](docs/API.md).

## Frontend Development

```bash
cd frontend
npm run dev       # Start dev server (http://localhost:5173)
npm run build     # Production build (Vite only; run `typecheck` separately)
npm run typecheck  # TypeScript type-check only (tsc -b)
npm run preview   # Preview production build
npm run lint      # ESLint
npm run format    # Prettier
```

The Vite dev server proxies `/api` (stripping the prefix) and `/files` to
`http://localhost:8000`. See [`vite.config.ts`](frontend/vite.config.ts) for details.

### Key dependencies

- **React 19** with TypeScript 5.7
- **React Router 7** for client-side routing
- **Vite 6** with HMR and the React plugin

## Project Structure

```
├── src/robotsix_file_hub/   # Python backend
│   ├── main.py              # App factory, lifespan, /health
│   ├── config.py            # pydantic-settings (FILE_HUB_ prefix)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models.py            # FileRecord ORM model
│   ├── schemas.py           # Pydantic request/response models
│   ├── routes/files.py      # All API endpoints (/files, /search, /reindex)
│   ├── storage.py           # Storage backends (local + S3)
│   ├── enrichment.py        # LLM enrichment (text extraction + AI summary)
│   ├── embeddings.py        # Local sentence-transformers embeddings
│   ├── tasks.py             # Background worker pool for enrichment
│   └── search.py            # Hybrid keyword + vector search
├── frontend/                # React SPA
│   ├── src/
│   │   ├── App.tsx          # Router + auth guard + nav
│   │   ├── AuthContext.tsx  # Token-based auth (localStorage)
│   │   ├── api.ts           # Typed API client
│   │   └── pages/           # HomePage, FilesPage, SearchPage, UploadPage, etc.
│   ├── vite.config.ts       # Vite config with /api proxy
│   └── package.json
├── tests/                   # pytest test suite
├── docs/
│   ├── API.md               # Detailed API reference
│   ├── deployment.md        # Deployment guide
│   └── modules.yaml         # Module manifest
├── pyproject.toml
├── .env.example             # Annotated configuration example
└── README.md
```

## Testing conventions

All test files must use the shared fixtures from [`tests/conftest.py`](tests/conftest.py)
(`test_client`, `test_db_session`, `test_storage`, `test_session_factory`)
instead of duplicating SQLAlchemy engine/session/client/storage setup inline.
File-local fixtures are acceptable only when monkey-patching module globals
(e.g., `tasks_module.async_session_factory`) — otherwise they create
maintenance duplication.

## Deployment

See [`docs/deployment.md`](docs/deployment.md) for production deployment
considerations (S3 storage, database migration, reverse proxy setup).

## License

Proprietary — Robotsix internal.
