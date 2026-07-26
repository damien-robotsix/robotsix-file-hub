# Changelog

## 0.0.0 (unreleased)

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
