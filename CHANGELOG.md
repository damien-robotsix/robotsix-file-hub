# Changelog

## 0.0.0 (unreleased)

- Scaffold React/Vite frontend in `frontend/` with TypeScript, React Router navigation, Vite dev-server proxy forwarding `/api/*` to the FastAPI backend, ESLint + Prettier linting/formatting, and a typed API client (`frontend/src/api.ts`) wrapping all backend endpoints.
- Added `POST /files/search` hybrid NL search endpoint combining keyword matching (filename, summary, tags) with optional vector similarity (cosine distance on embeddings). Falls back to keyword-only when embeddings are unavailable. Returns paginated results with relevance scores.
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
