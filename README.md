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
                       │  • Keyword search  │──────│   Local FS   │
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
  any SQLAlchemy-supported database by changing `database_url`.
- **Storage** — local filesystem storage.
- **AI Pipeline** — uses **robotsix-llmio** (OpenRouter) for LLM enrichment
  (defaults to tier level 1 for cheap extraction) and a dedicated
  OpenAI-compatible embedding server (shared bge-m3, 1024-dim). Supports
  text-based files (PDF, DOCX, XLSX, plain text) and images (PNG, JPEG,
  etc.) via vision-capable LLMs.

For a deeper dive into the internal design, see **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Quick Start

### Prerequisites

- Python ≥ 3.14
- [uv](https://docs.astral.sh/uv/) package manager
- Node.js ≥ 18 (for the frontend)
- [Docker](https://docs.docker.com/compose/) (optional, for running the
  backend)
- (Optional) [Ollama](https://ollama.com/) or any OpenAI-compatible LLM API
  for file enrichment

### Docker Compose (recommended)

The easiest way to run the backend is via Docker Compose. It starts Postgres
alongside the backend, and mounts `config/config.docker.json` so the
backend uses Postgres for metadata and local filesystem for file storage:

```bash
# Clone and enter the project
git clone <repo-url> && cd robotsix-file-hub

# Start the backend
docker compose up --build
```

The backend API is now live at `http://localhost:8000`. Visit `/health/live` for
a lightweight liveness probe and `/docs` for the interactive OpenAPI docs.

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

The API is now live at `http://localhost:8000`. Visit `/health/live` for a
lightweight liveness probe and `/docs` for the interactive OpenAPI docs.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server starts on `http://localhost:5173` and proxies `/api`
and `/files` requests to the backend at `http://localhost:8000`.

## Configuration

All settings are read from a single JSON file — `config/config.json` by
default, or the path named by the `ROBOTSIX_CONFIG_FILE` environment
variable. There is no environment overlay. See
[`docs/core/configuration.md`](docs/core/configuration.md) for the full list of keys,
types, and defaults.

## API Reference

Base URL: `http://localhost:8000`

| Method | Path | Description |
|---|---|---|
| `GET` | `/health/live` | Lightweight liveness probe — returns `{"status":"ok"}` (no dependencies) |
| `GET` | `/health` | Readiness probe — checks DB + storage connectivity |
| `GET` | `/deploy-spec` | Deploy spec for central-deploy — returns `deploy/docker-compose.yml` with contract-version header |
| `POST` | `/files` | Upload a single file (`multipart/form-data`, field `file`) |
| `POST` | `/files/batch` | Upload multiple files (field `files`) |
| `GET` | `/files` | List files with pagination and filters (`?category=`, `?tag=`, `?offset=`, `?limit=`, etc.) |
| `GET` | `/files/categories` | Return distinct, sorted categories across all files |
| `GET` | `/files/{file_id}` | Download raw file bytes (forced download, `attachment`) |
| `GET` | `/files/{file_id}/view` | View file inline in browser (PDF/image rendering, `inline`) |
| `GET` | `/files/{file_id}/metadata` | Get file metadata (category, tags, summary, etc.) |
| `DELETE` | `/files/{file_id}` | Delete a file and its stored data |
| `POST` | `/files/search` | Hybrid NL search — JSON body `{"query":"…","offset":0,"limit":50}` |
| `POST` | `/files/reindex` | Re-enqueue enrichment for existing files |
| `GET` | `/files/reindex/progress` | Reindex progress (`total`, `completed`, `failed`, `active`, `task_id`) |
| `GET` | `/tasks/{task_id}` | Poll enrichment/reindex task status (`type`, `status`, `progress`, `error`) |

Full request/response schemas are available in the interactive docs at
`/docs` (Swagger UI) and in [`docs/core/API.md`](docs/core/API.md).

## Frontend Development

```bash
cd frontend
npm run dev       # Start dev server (http://localhost:5173)
npm run build     # Production build (Vite only; run `typecheck` separately)
npm run typecheck  # TypeScript type-check only (tsc -b)
npm run preview   # Preview production build
npm run lint      # ESLint
npm run format    # Prettier
npm test          # Run Vitest test suite
npm run test:watch  # Vitest in watch mode
npm run test:coverage  # Vitest with coverage report
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
│   ├── config.py            # JSON config (config.json)
│   ├── database.py          # Async SQLAlchemy engine + session
│   ├── models.py            # FileRecord ORM model
│   ├── schemas.py           # Pydantic request/response models
│   ├── routes/files.py      # All API endpoints (/files, /search, /reindex)
│   ├── storage.py           # Local filesystem storage
│   ├── enrichment.py        # LLM enrichment (text/image extraction + AI summary)
│   ├── embeddings.py        # Embedding text shaping (delegates to enrichment.py)
│   ├── tasks.py             # Background worker pool for enrichment
│   └── search.py            # Hybrid keyword + vector search
├── frontend/                # React SPA
│   ├── src/
│   │   ├── App.tsx          # Router + nav
│   │   ├── api.ts           # Typed API client
│   │   └── pages/           # HomePage, FilesPage, SearchPage, UploadPage, etc.
│   ├── vite.config.ts       # Vite config with /api proxy
│   └── package.json
├── tests/core/              # pytest test suite
├── docs/
│   ├── core/
│   │   ├── API.md           # Detailed API reference
│   │   ├── configuration.md # Configuration reference
│   │   └── deployment.md    # Deployment guide
│   └── modules.yaml         # Module manifest
├── pyproject.toml
├── .env.example             # Optional ROBOTSIX_CONFIG_FILE override
└── README.md
```

## Frontend conventions

> **Rule:** Display file sizes through the shared `formatSize` helper in
> `frontend/src/lib/format.ts` (which handles B/KB/MB dynamically); never
> define local byte-formatting functions or inline `(n/1024).toFixed(1)`"KB"
> expressions.

## Testing conventions

All test files must use the shared fixtures from [`tests/core/conftest.py`](tests/core/conftest.py)
(`test_client`, `test_db_session`, `test_storage`, `test_session_factory`)
instead of duplicating SQLAlchemy engine/session/client/storage setup inline.
File-local fixtures are acceptable only when monkey-patching module globals
(e.g., `tasks_module.async_session_factory`) — otherwise they create
maintenance duplication.

## Deployment

See [`docs/core/deployment.md`](docs/core/deployment.md) for production deployment
considerations (database migration, reverse proxy setup).

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** for developer setup, coding
conventions, and the pull request process.

## License

Proprietary — Robotsix internal.
