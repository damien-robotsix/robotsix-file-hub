# Contributing — robotsix-file-hub

## Development environment

### Prerequisites

- **Python ≥ 3.14** — managed with [uv](https://docs.astral.sh/uv/).
- **Node.js ≥ 18** — managed with the Node.js version manager of your
  choice.
- **[Docker](https://docs.docker.com/compose/)** — optional, for
  PostgreSQL (the Docker Compose stack).
- **[Ollama](https://ollama.com/)** — optional, for file enrichment.

### Backend setup

```bash
# Clone the repo
git clone <repo-url> && cd robotsix-file-hub

# Install Python dependencies (creates a .venv)
uv sync
```

The project uses a `src/` layout.  After `uv sync`, the package
`robotsix_file_hub` is importable.  No `pip install -e .` step is
needed; `uv sync` handles the editable install via `[tool.uv.sources]`.

### Frontend setup

```bash
cd frontend
npm ci          # clean install from package-lock.json
npm run dev     # start Vite dev server at http://localhost:5173
```

The Vite dev server proxies `/api` and `/files` to
`http://localhost:8000`, so you need the backend running for the
frontend to work fully.

### Database

In development, tables are created automatically on startup via
`Base.metadata.create_all`.  No manual migration step is required.

For production or when testing migrations explicitly:

```bash
uv run alembic upgrade head
```

### Configuration

Settings are read from `config/config.json` (or the path named by the
`ROBOTSIX_CONFIG_FILE` environment variable).  There is no environment
overlay.  The committed defaults target the container layout; for local
development, point `ROBOTSIX_CONFIG_FILE` at a file using relative paths
(SQLite, local filesystem storage, Ollama on localhost).

---

## Code quality

The CI pipeline enforces every tool listed below.  Run them locally
before pushing to avoid surprises.

### Backend (Python)

| Tool | Command | Checks |
|---|---|---|
| **ruff** (lint) | `uv run ruff check .` | Linting: style, imports, best practices |
| **ruff** (format) | `uv run ruff format --check .` | Formatting consistency |
| **mypy** | `uv run mypy src/` | Static type checking (`--strict`) |
| **deptry** | `uv run deptry src/` | Import hygiene (missing/unused dependencies) |
| **vulture** | `uv run vulture --ignore-decorators "@field_validator,@model_validator" src/ vulture_whitelist.py` | Dead code detection |

The `pyproject.toml` configures:
- **ruff** — line length 100, Python 3.14 target, rules `E,F,I,N,W,UP,B,SIM,C4`.
- **mypy** — strict mode, Python 3.14 target.

### Frontend (TypeScript / React)

| Tool | Command | Checks |
|---|---|---|
| **TypeScript** | `npm run typecheck` | Type checking (`tsc -b`) |
| **ESLint** | `npm run lint` | Linting (flat config with TS + React plugins) |
| **Prettier** | `npm run format` | Formatting (applies fixes; CI only checks) |

---

## Running tests

### Backend

```bash
# Run the full backend test suite
uv run pytest

# Run a single test file
uv run pytest tests/core/test_search.py

# Run with verbose output
uv run pytest -v
```

Tests use **pytest-asyncio** in `auto` mode and share fixtures from
`tests/core/conftest.py`.  Each test gets an isolated database session that
is rolled back after the test completes.

### Frontend

```bash
cd frontend

# Run the full frontend test suite
npm test

# Watch mode (re-run on file changes)
npm run test:watch

# With coverage report
npm run test:coverage
```

Tests use **Vitest** with `@testing-library/react` and `happy-dom` as
the DOM environment.  Test files live alongside their source
(`*.test.ts`, `*.test.tsx`).

---

## Pull request workflow

1. **Create a branch** from `main`.  Use a descriptive kebab-case name
   (e.g. `fix-search-pagination`, `add-preview-for-xlsx`).

2. **Make your changes.**  Keep commits focused and atomic.  Follow the
   existing code style — the linters above are the authority.

3. **Run the quality gates locally:**

   ```bash
   # Backend
   uv run ruff check . && uv run ruff format --check .
   uv run mypy src/
   uv run pytest

   # Frontend
   cd frontend && npm run typecheck && npm run lint && npm test
   ```

4. **Push your branch** and open a pull request against `main`.

5. **CI checks** — the following jobs run automatically on every PR:
   - **Python CI** — lint (ruff), type-check (mypy), test (pytest),
     coverage threshold (80%).
   - **Supply chain** — dependency audit (`uv audit`) and import
     hygiene (`deptry`).
   - **Quality** — duplicate of lint + type-check + test (runs as a
     standalone job).
   - **Frontend** — TypeScript type-check + ESLint lint.

6. **Review** — a maintainer will review your PR.  Address feedback by
   pushing additional commits; squash-merging is done on acceptance.

---

## Project conventions

### Python

- **Imports** — use absolute imports from `robotsix_file_hub.*`.  Ruff
  (`I` rule) enforces import sorting.
- **Type annotations** — required everywhere; mypy runs in `--strict`
  mode.  Use `from __future__ import annotations` for PEP 604 syntax.
- **Async** — all I/O (database, HTTP, filesystem) uses `async`/`await`.
- **Configuration** — never read `os.environ` directly; use the
  `get_settings()` singleton from `robotsix_file_hub.config`.
- **Error handling** — enrichment and embedding are best-effort.  A
  failure leaves fields null rather than failing the upload.

### Frontend

- **File sizes** — always use the shared `formatSize` helper from
  `frontend/src/lib/format.ts`.  Never define local byte-formatting
  functions or inline `(n / 1024).toFixed(1)` expressions.
- **API calls** — use the typed client in `frontend/src/api.ts` rather
  than raw `fetch` calls.
- **Components** — one component per file in `pages/` or `components/`.
  Co-locate CSS and test files (`*.css`, `*.test.tsx`).

### Testing

- **Backend** — use shared fixtures from `tests/core/conftest.py`.  Do not
  duplicate SQLAlchemy engine/session/client setup in individual test
  files.
- **Frontend** — prefer `@testing-library/react` queries (`getByRole`,
  `getByText`) over direct DOM manipulation.  Use `happy-dom` (already
  configured).

---

## Further reading

- [README](README.md) — project overview, quick start, API reference.
- [ARCHITECTURE.md](ARCHITECTURE.md) — internal design and module layout.
- [docs/core/API.md](docs/core/API.md) — detailed request/response schemas.
- [docs/core/deployment.md](docs/core/deployment.md) — production deployment.
- [docs/core/configuration.md](docs/core/configuration.md) — configuration reference.
