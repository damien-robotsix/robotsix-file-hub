# Changelog

## 0.0.0 (unreleased)

- Fix pydantic `NameError` caused by `BaseSettings.__init__` underscore-prefixed
  parameters being treated as body-model fields.  Replace `Depends(Settings)` with
  a wrapper factory `get_settings()` so FastAPI analyses the factory signature
  (no parameters) instead of the pydantic-settings `__init__` signature.
- Consolidate duplicate search endpoints: frontend now calls `POST /search` instead of the removed `POST /files/search`, and the duplicate handler in `routes/files.py` has been removed with its docstring updated accordingly.
- Add frontend test infrastructure with Vitest, Testing Library, and jsdom
  - devDependencies: vitest, @testing-library/react, @testing-library/jest-dom,
    @testing-library/user-event, jsdom, @vitest/coverage-v8
  - Test scripts: `npm test`, `npm run test:watch`, `npm run test:coverage`
  - Tests for `formatSize`, `classifyPreview`, `escapeHtml`,
    `tokenStorage`, `AuthContext`, `api` client (smoke tests covering
    auth injection, error handling, and key endpoints), and
    `FilesPage` (basic component render with router context)
- Updated `src/robotsix_file_hub/routes/__init__.py` docstring to accurately describe all route modules (files, search, and tasks) instead of only "File upload routes."
- Split `tests/test_search.py` (1532 lines) into three focused modules: `test_search_keyword.py` (keyword/cosine/hybrid scoring unit tests), `test_search_pg.py` (search orchestration tests covering the `search_files_pg` fallback path), and `test_search.py` (integration/endpoint tests). Extracted `_metadata_filter_conditions` helper in `search.py` to deduplicate repeated filter-application blocks.
- Add "Frontend conventions" section to README documenting the `formatSize` helper rule (centralize file-size display through `frontend/src/lib/format.ts`)
- Removed dead `getStoredToken` export from `frontend/src/tokenStorage.ts` (it was never imported or used)
- Eliminate duplicate `formatBytes` function in `FilesPage.tsx` and inline KB formatting in `HomePage.tsx` — both now use the shared `formatSize` from `frontend/src/lib/format.ts`
- Replace hand-rolled HTTP retry logic with `robotsix-http.acall_with_retry` and `RetryConfig` in `enrichment.py`.
- Resolve deptry DEP002/DEP003 findings: consolidate dev dependencies into `[dependency-groups].dev`, remove legacy `[project.optional-dependencies]`, and shrink `per_rule_ignores` to only legitimate no-import runtime packages.
- Enable mypy_baseline periodic workflow to track mypy baseline errors across builds.
- Switch Dependabot ecosystem from `pip` to `uv` so it understands the `uv.lock` file, and add `github-actions` ecosystem with grouping to reduce PR noise.
- Remove `robotsix-http` dependency and inline retry-with-backoff logic into `enrichment.py`.
- CI: fix pre-existing deptry supply-chain failures by adding `pydantic` to direct dependencies and configuring `[tool.deptry]` with per-rule ignores for implicit dependencies (`aiosqlite`, `asyncpg`, `uvicorn`, `python-multipart`, `ruff`, `mypy`, `pytest-asyncio`) and a package-module name mapping for `python-docx` → `docx`.
- Add `FILE_HUB_LOG_LEVEL` to the README configuration table and create `docs/configuration.md` consolidating all settings with types, defaults, and descriptions.
- Remove dead `_parse_embedding` legacy shim from `search.py` — a JSON-string fallback that predated the pgvector migration and was unreachable at runtime.
- Add supply-chain security scanning to CI: `uv audit --frozen` vulnerability check, `deptry` dependency hygiene, `UV_MALWARE_CHECK=1` during install, and Dependabot configuration for pip and npm ecosystems.
- Rename `storage_path` → `storage_key` in the frontend `FileMetadata` interface and fix the batch-upload response mapping to pass through the actual backend field value instead of hardcoding an empty string.
- Eliminated ~150 lines of code duplication between `FilePreview.tsx` and `FileDetailPage.tsx`: extracted `classifyPreview`, `escapeHtml`, and `PreviewKind` into `frontend/src/lib/preview.ts`; `FileDetailPage` now reuses `FilePreview` with `showHeader={false}` and `showMeta={false}` props.
- Remove five dead `api.ts` exports (`uploadFileWithProgress`, `uploadFiles`, `downloadFile`, `setAuthToken`, `clearAuthToken`) — each superseded by a used sibling helper already wired into the UI.
- Reindex button and live progress indicator added to the Files page, wiring the existing `POST /files/reindex` and `GET /files/reindex/progress` backend endpoints to the UI.
- Wire delete-file UI: add Delete buttons to FilesPage table rows, FilePreview modal header, and FileDetailPage, backed by `deleteFile` with `X-Confirm-Delete` header and `window.confirm` guard.
- Add `robotsix-http` retry/backoff for upstream LLM API calls in `enrichment.py`, so transient errors (429 rate-limit, 503 unavailable, connection resets) are retried with exponential backoff instead of failing immediately.
- Bootstrap `.robotsix-mill/periodic/` with presence files for 14 periodic workflows (audit, health, survey, changelog_autofill, repo_description_sync, completeness_check, copy_paste, docstring_coverage, test_gap, module_curator, module_size, bc_check, agent_check, triage_boilerplate)
- Fix invalid commit SHA in `.github/workflows/docker-publish.yml` for `docker-release` reusable workflow (`damien-robotsix/robotsix-github-workflows`).
- Add `get_current_user` FastAPI dependency that extracts and validates
  bearer tokens / API keys and returns the authenticated user identity
- Add `X-API-Key` header support as an alternative to `Authorization: Bearer` for authentication.
  Invalid tokens now return 401 Unauthorized (was 403).
- Docker Compose development environment (backend, postgres+pgvector, minio) already in place; no changes needed for this foundation ticket.
- Fix missing favicon: add `frontend/public/vite.svg` so the link in `index.html` resolves.
- Add MIT LICENSE file at repo root and declare `license = {text = "MIT"}` in pyproject.toml.
- Add `GET /deploy-spec` endpoint that serves `deploy/docker-compose.yml` with the `central-deploy-contract-version: 1` response header, enabling central-deploy component registration.
- Remove duplicate `DELETE /{file_id}` handler; consolidate into single guarded handler with correct DB-first ordering (404 check before confirmation, storage delete after DB commit)
- Fix `search_files_pg` 500 error when embedding generation fails: guard the hybrid_score expression so the vector component is only included when `query_embedding is not None`, falling back to keyword-only scoring.
- Fix broken PostgreSQL-native hybrid search path in `search_files_pg`: replace bare `sa_text("ts_rank")` (no arguments) with proper `func.ts_rank(...)` call; remove dead `base_cols` block; add explicit `bindparam` typing for the pgvector `<=>` operator's `:query_embedding` parameter via `pgvector.sqlalchemy.Vector`.
- Fix ruff violations (import sorting, `Union` → `X | Y`, B018/F821) in migration files and vulture whitelist
- Add `DELETE /files/{file_id}` endpoint to delete stored files and their metadata.
- Decouple TypeScript type-checking from the Docker image build: `frontend` build script now runs `vite build` only (no `tsc -b` gate). A separate `typecheck` script (`tsc -b`) is available for local/CI use.
- Fix TypeScript build error in `frontend/src/api.ts` by removing unused `embedding` property from `uploadFileWithProgress` response mapping. The `FileMetadata` interface does not declare `embedding`, and the backend does not return it in file upload responses.
- Fix Docker image build failure: split `uv sync` in builder stage to install
  dependencies first (`--no-install-project`) before copying source, so hatchling
  can find the project directory during the project install step.
- Add multi-stage Dockerfile with frontend build, uv-based Python install, and HEALTHCHECK.
- Add `deploy/docker-compose.yml` for central-deploy (GHCR image, named volumes, `robotsix.deploy.*` labels).
- Add `.github/workflows/docker-publish.yml` using the fleet shared reusable publish workflow.
- Add `log_level` config field with UTC ISO-8601 stdout logging.
- Document testing conventions in README: all test files must use shared
  fixtures from `tests/conftest.py` instead of duplicating SQLAlchemy
  engine/session/client/storage setup inline.
- Add Docker Compose quick-start instructions to README (clone, copy `.env.example`, `docker compose up`, frontend access)
- Upload dialog now sends all files in a single batch `POST /files/batch` request with per-file progress estimation, and an "+ Upload" button on the Files page opens the dialog.
- Added `GET /files/categories` endpoint returning distinct, sorted categories
- Changed category filter on the Files page from a free-text input to a dropdown populated from the categories endpoint
- Added page-number buttons (with ellipsis for large page counts) to the Files page pagination controls
- Category and tag badges now styled as colored badges on each file row in the Files table
- Add `/files` Vite dev-server proxy alongside existing `/api` proxy, so backend file routes are reachable via both prefixes during development.
- Add `setAuthToken`, `clearAuthToken`, and `deleteFile` to the typed API client (`frontend/src/api.ts`).
- Add inline file preview panel: clicking a file in search results or file list opens an in-page preview with rendered images, PDFs (first page), and text files. The preview panel includes a download button and can be closed to return to the list.
- Add pgvector support for storing embeddings as native vector(384) columns instead of JSON, including migration to enable the pgvector extension and alter the embedding column type.
- Enhance `GET /health` endpoint with DB and storage connectivity checks returning `{"status": "ok"|"degraded", "db": "...", "storage": "..."}`; no auth required.
- Add guarded `DELETE /files/{id}` endpoint with confirmation header (`X-Confirm-Delete: true`) or query param (`?confirm=true`); returns 204 on success and 404 when file not found.
- Add per-task status tracking to the background worker pool (pending, running, completed, failed)
- Add `GET /tasks/{id}` endpoint to poll for job status
- Return `task_id` from upload and reindex endpoints so clients can track enrichment progress
- Add `TaskResponse`, `TaskType`, and `TaskStatus` Pydantic schemas
- Wire bearer-token authentication to all file endpoints; when `FILE_HUB_AUTH_TOKEN` is set, requests must include `Authorization: Bearer <token>`
- Add `auth_token` setting to the `Settings` class for API bearer-token authentication, with an empty-string default (no auth) for development.
- Rename `FileRecord.storage_path` column to `storage_key` and add `updated_at` column. Rename table from `files` to `file_records`.
- Add Docker Compose dev environment with backend, Postgres+pgvector, and MinIO services
- Fix uploaded file size displaying as 0 by correcting the field name mapping (`size_bytes` → `size`) in `uploadFileWithProgress` and `UploadDialog`.
- Extract duplicated `formatSize` helper into shared `frontend/src/lib/format.ts`, imported by all three callers (SearchPage, FileDetailPage, UploadDialog).
- Fix pre-existing mypy type errors in search, embeddings, and tasks modules
  (unused type-ignore comments, type mismatch in `_hybrid_score`'s embedding
  handling, and dead assignment to `record.embedding` in the enrichment worker).
- `POST /files/batch` now uses an all-or-nothing transaction: if any file in the
  batch fails, all prior files are rolled back (both DB records and stored bytes
  are cleaned up).  Previously each file was committed individually, leaving
  orphan records on mid-batch failures.
- Fix CI failures in pre-existing code: ruff format (PEP 758 except syntax), Prettier formatting (10 frontend files), and ESLint react-refresh warnings (extracted token utilities to tokenStorage.ts)
- Add shared pytest fixtures (`tests/conftest.py`) with in-memory SQLite DB, local storage backend, and mock LLM enrichment. Add health endpoint tests (`tests/test_health.py`).
- Add comprehensive documentation: architecture overview, quick-start
  guide, configuration reference, API reference, and deployment guide.
  Includes `.env.example` with all config variables, `docs/API.md` with
  full endpoint schemas, and `docs/deployment.md` with production
  setup instructions.
- FileDetailPage: reset text preview state when navigating between files to avoid stale/fetched content race condition
  - api.ts: rename `listFiles` param `skip` to `offset` for consistency with query key and API interfaces
- Add search box in the top navigation bar that accepts natural-language queries
- Build search results page with relevance scores, file metadata, and preview thumbnails
- Add file detail/preview page with inline display for images, PDFs, and text files, plus fallback for unsupported types
- Add download button on the file detail page
- Fix frontend API paths to align with backend `/files` route prefix
- Add authentication UI with login page and React context provider, storing
  API token in localStorage and protecting all routes behind an auth guard.
- Add file browser page with table view, pagination, and filter controls
  (category, tag, content-type, date range).  Files display content-type
  icons, size, upload date, and LLM category/tags when available.
- Add file detail/preview page showing full metadata, download link, and
  text-file preview.
- Align frontend API types and route paths with backend schemas (FileMetadata,
  SearchResult, `/files` prefix on all file endpoints).
- Fix upload dialog: disable drop zone during active upload to prevent straggler files; fix drag-leave flicker by checking `relatedTarget`; remove dead `{ kind: "pending" }` union variant
- Add upload dialog to the file browser: toolbar "Upload" button opens a modal
  with drag-and-drop zone, multi-file selection, per-file progress bars (using
  XMLHttpRequest progress events), and success/error display. File list
  auto-refreshes after upload completes.
- Scaffold React/Vite frontend in `frontend/` with TypeScript, React Router navigation, Vite dev-server proxy forwarding `/api/*` to the FastAPI backend, ESLint + Prettier linting/formatting, and a typed API client (`frontend/src/api.ts`) wrapping all backend endpoints.
- Added `POST /files/search` hybrid NL search endpoint combining keyword matching (filename, summary, tags) with optional vector similarity (cosine distance on embeddings). Falls back to keyword-only when embeddings are unavailable. Returns paginated results with relevance scores.
- Add vector embedding generation for hybrid search using sentence-transformers (all-MiniLM-L6-v2). Embeddings are generated from concatenated file metadata (filename + summary + tags + category) during enrichment and stored in the `FileRecord.embedding` JSON column. Embeddings are regenerated on re-index.
- Add filtering support to `POST /files/reindex` (category, content_type, file_ids) and progress tracking via `GET /files/reindex/progress` (total, completed, failed, active).
- LLM enrichment pipeline: on file upload, extract text from common formats (PDF, plain text, DOCX, XLSX) and call a configurable OpenAI-compatible LLM to generate summary, category, and tags. Enrichment is best-effort — fields are left null if text extraction or the LLM call fails.
- Add background task queue with asyncio workers for async file processing.
  Enrichment tasks (categorization, tagging, summarization) are enqueued
  fire-and-forget after each upload. A ``POST /files/reindex`` endpoint
  re-enqueues enrichment for all existing files. Workers start/stop with
  the FastAPI lifespan.
- Add download, metadata, and list endpoints: `GET /files/{id}` streams raw bytes, `GET /files/{id}/metadata` returns full record JSON, `GET /files` lists with filters (category, tag, content_type, source, before/after) and offset/limit pagination. Add enrichment columns (category, tags, summary, source) to FileRecord.
- Add file upload endpoints: `POST /files` (single) and `POST /files/batch`
  with storage backend abstraction (S3/MinIO via boto3 and local filesystem fallback)
- Set up Python package scaffold: `pyproject.toml`, `src/robotsix_file_hub/` with minimal FastAPI app stub, and dev tooling config (ruff, mypy, pytest)
