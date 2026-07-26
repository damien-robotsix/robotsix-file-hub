# API Reference

Base URL: `http://localhost:8000`

All file-related endpoints are mounted under `/files` and use JSON
request/response bodies unless noted otherwise.  Task-status endpoints
are mounted under `/tasks`.  Interactive docs are available at `/docs`
(Swagger UI).

---

## Authentication

All `/files/*` endpoints require Bearer-token authentication when
`FILE_HUB_AUTH_TOKEN` is configured (see [`.env.example`](../.env.example)).

| Header | Value |
|---|---|
| `Authorization` | `Bearer <token>` |

- **`401`** — Missing or invalid `Authorization` header.
- **`403`** — Token present but does not match the configured `auth_token`.

When `FILE_HUB_AUTH_TOKEN` is empty (default in development), authentication
is skipped — all requests are accepted without credentials.

---

## Health

### `GET /health`

Returns a liveness check.

**Response** `200`

```json
{"status": "ok"}
```

---

## Files

### `POST /files`

Upload a single file.

- **Content-Type:** `multipart/form-data`
- **Form field:** `file` (required)

**Response** `200` — [`FileUploadResponse`](#fileuploadresponse)

```json
{
  "id": "uuid",
  "filename": "report.pdf",
  "size": 204800,
  "content_type": "application/pdf",
  "checksum": "sha256hex…",
  "created_at": "2025-01-01T00:00:00Z",
  "task_id": "uuid"
}
```

**Errors:** `413` (file too large), `500` (storage or database failure)

---

### `POST /files/batch`

Upload multiple files in one request.

- **Content-Type:** `multipart/form-data`
- **Form field:** `files` (required, multiple)

**Response** `200` — [`BatchUploadResponse`](#batchuploadresponse)

```json
{
  "files": [
    {
      "id": "uuid",
      "filename": "report.pdf",
      "size": 204800,
      "content_type": "application/pdf",
      "checksum": "sha256hex…",
      "created_at": "2025-01-01T00:00:00Z",
      "task_id": "uuid"
    }
  ]
}
```

**Errors:** `413`, `500`

---

### `GET /files`

List files with optional filters and pagination.

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `offset` | int ≥ 0 | `0` | Pagination offset |
| `limit` | int 1–1000 | `50` | Page size |
| `category` | string | — | Exact match on category |
| `tag` | string | — | Substring match on tags |
| `content_type` | string | — | Exact match on MIME type |
| `source` | string | — | Exact match on source/uploader |
| `before` | ISO 8601 datetime | — | Files created before this timestamp |
| `after` | ISO 8601 datetime | — | Files created after this timestamp |

**Response** `200` — [`FileListResponse`](#filelistresponse)

```json
{
  "files": [ … ],
  "total": 42,
  "offset": 0,
  "limit": 50
}
```

---

### `GET /files/{file_id}`

Download the raw file bytes.

- **Response headers** include `Content-Disposition: attachment` and
  `Content-Length`.

**Response** `200` — binary stream

**Errors:** `404` (file not found), `500` (storage failure)

---

### `GET /files/{file_id}/metadata`

Return the full metadata record for a stored file, including enrichment
fields (category, tags, summary, source) after the AI pipeline completes.

**Response** `200` — [`FileMetadataResponse`](#filemetadataresponse)

**Errors:** `404`

---

## Search

### `POST /files/search`

Hybrid natural-language search combining keyword matching with vector
similarity.  Falls back to keyword-only ranking when embeddings are
unavailable.

**Request body** — [`SearchRequest`](#searchrequest)

```json
{
  "query": "quarterly financial reports",
  "offset": 0,
  "limit": 50
}
```

**Response** `200` — [`SearchResponse`](#searchresponse)

```json
{
  "results": [
    {
      "id": "uuid",
      "filename": "Q4-report.pdf",
      "relevance": 0.92,
      …metadata fields
    }
  ],
  "total": 3,
  "offset": 0,
  "limit": 50,
  "query": "quarterly financial reports"
}
```

**Errors:** `500`

---

## Reindex

### `POST /files/reindex`

Re-enqueue enrichment jobs for existing files.  Useful after changing the
LLM model or enrichment logic.  Returns a `task_id` that can be polled
via `GET /tasks/{task_id}` for overall batch progress.

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `category` | string | Only reindex files with this category |
| `content_type` | string | Only reindex files with this MIME type |
| `file_ids` | string | Comma-separated file IDs to reindex |

**Response** `200`

```json
{"enqueued": 100, "task_id": "uuid"}
```

**Errors:** `500`

---

### `GET /files/reindex/progress`

Return the current reindex operation progress.

**Response** `200`

```json
{
  "total": 100,
  "completed": 45,
  "failed": 2,
  "active": true,
  "task_id": "uuid"
}
```

---

## Tasks

### `GET /tasks/{task_id}`

Poll the status of a background task (enrichment or reindex).

Returns the task type, current status, optional error message, and
timestamps.  For reindex tasks, `progress` shows the percentage of
individual enrichment jobs completed.

**Response** `200` — [`TaskResponse`](#taskresponse)

```json
{
  "task_id": "uuid",
  "type": "enrichment",
  "status": "completed",
  "file_id": "uuid",
  "progress": null,
  "error": null,
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:01Z"
}
```

**Errors:** `404` (task not found)

---

## Schemas

### `FileUploadResponse`

| Field | Type | Description |
|---|---|---|
| `id` | string (UUID) | Unique file identifier |
| `filename` | string | Original filename |
| `size` | int | File size in bytes |
| `content_type` | string | MIME type |
| `checksum` | string | SHA-256 hex digest |
| `created_at` | datetime | Upload timestamp (ISO 8601) |
| `task_id` | string (UUID) \| null | Background enrichment task ID (poll via `GET /tasks/{task_id}`) |

### `FileMetadataResponse`

All fields from `FileUploadResponse` plus:

| Field | Type | Description |
|---|---|---|
| `storage_key` | string | Internal storage key (path or object key) |
| `updated_at` | datetime | Last-update timestamp (ISO 8601) |
| `category` | string \| null | AI-assigned category |
| `tags` | string \| null | AI-assigned comma-separated tags |
| `summary` | string \| null | AI-generated summary |
| `source` | string \| null | Uploader / source identifier |

### `FileListResponse`

| Field | Type | Description |
|---|---|---|
| `files` | `FileMetadataResponse[]` | Result page |
| `total` | int | Total matching files |
| `offset` | int | Current offset |
| `limit` | int | Current page size |

### `ErrorResponse`

| Field | Type | Description |
|---|---|---|
| `detail` | string | Human-readable error message |

### `SearchRequest`

| Field | Type | Default | Description |
|---|---|---|---|
| `query` | string | *(required)* | Natural-language search query |
| `offset` | int ≥ 0 | `0` | Pagination offset |
| `limit` | int 1–1000 | `50` | Page size |

### `SearchResult`

All metadata fields plus:

| Field | Type | Description |
|---|---|---|
| `relevance` | float | 0–1 relevance score (higher = better match) |

### `SearchResponse`

| Field | Type | Description |
|---|---|---|
| `results` | `SearchResult[]` | Ranked search results |
| `total` | int | Total matching files |
| `offset` | int | Current offset |
| `limit` | int | Current page size |
| `query` | string | Echo of the search query |

### `BatchUploadResponse`

| Field | Type | Description |
|---|---|---|
| `files` | `FileUploadResponse[]` | Upload results for each file |

### `TaskResponse`

| Field | Type | Description |
|---|---|---|
| `task_id` | string (UUID) | Unique task identifier |
| `type` | `"enrichment"` \| `"reindex"` | Task type |
| `status` | `"pending"` \| `"running"` \| `"completed"` \| `"failed"` | Current status |
| `file_id` | string (UUID) \| null | File being enriched (nullable for reindex tasks) |
| `progress` | int \| null | Reindex completion percentage (0–100), null for single enrichment tasks |
| `error` | string \| null | Error message if status is `failed` |
| `created_at` | datetime | Task creation timestamp (ISO 8601) |
| `updated_at` | datetime | Last status-update timestamp (ISO 8601) |
