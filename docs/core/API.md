# API Reference

Base URL: `http://localhost:8000`

All file-related endpoints are mounted under `/files` and use JSON
request/response bodies unless noted otherwise.  Task-status endpoints
are mounted under `/tasks`.  The DELETE method on `/files/{file_id}`
permanently removes the file record and its stored data.  Interactive
docs are available at `/docs` (Swagger UI).

---

## Health

### `GET /health/live`

Lightweight liveness probe. Returns immediately without checking any
dependencies (database, storage). Used by Docker HEALTHCHECK.

**Response** `200`

```json
{"status": "ok"}
```

### `GET /health`

Readiness probe. Checks database connectivity and storage backend health.

**Response** `200`

```json
{
  "status": "ok",
  "db": "ok",
  "storage": "ok"
}
```

---

## Deploy Spec

### `GET /deploy-spec`

Returns the deploy specification (`deploy/docker-compose.yml`) for
central-deploy component registration.  The response includes the
`central-deploy-contract-version: 1` header, which central-deploy
requires to validate the spec during component registration.

No authentication is required.

**Response** `200`

- **Content-Type:** `application/x-yaml`
- **Headers:** `central-deploy-contract-version: 1`
- **Body:** YAML content of `deploy/docker-compose.yml`

---



## Files

### `POST /files`

Upload a single file.

- **Content-Type:** `multipart/form-data`
- **Form field:** `file` (required)

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `allow_duplicate` | bool | `false` | If `true`, bypasses content-dedup and always stores a new copy; default dedup reuses an existing record when content checksum matches |

By default, uploading the same content twice **deduplicates**: the second
call returns the existing file's id with `deduplicated: true` and does
not store additional bytes.

**Response** `200` — [`FileUploadResponse`](#fileuploadresponse)

```json
{
  "id": "uuid",
  "filename": "report.pdf",
  "size": 204800,
  "content_type": "application/pdf",
  "checksum": "sha256hex…",
  "created_at": "2025-01-01T00:00:00Z",
  "task_id": "uuid",
  "deduplicated": false
}
```

**Errors:** `413` (file too large), `500` (storage or database failure)

---

### `POST /files/batch`

Upload multiple files in one request.

- **Content-Type:** `multipart/form-data`
- **Form field:** `files` (required, multiple)

**Query parameters**

| Param | Type | Default | Description |
|---|---|---|---|
| `allow_duplicate` | bool | `false` | If `true`, bypasses content-dedup for all files in the batch; default dedup reuses existing records on checksum match |

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
      "task_id": "uuid",
      "deduplicated": false
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

### `GET /files/categories`

Return a sorted list of distinct categories across all files.

**Response** `200` — [`CategoriesResponse`](#categoriesresponse)

```json
{
  "categories": ["document", "image", "spreadsheet"]
}
```

---

### `GET /files/{file_id}`

Download the raw file bytes (forced download).

- **Response headers** include `Content-Disposition: attachment` and
  `Content-Length`.

**Response** `200` — binary stream

**Errors:** `404` (file not found), `500` (storage failure)

---

### `GET /files/{file_id}/view`

Serve the file with inline disposition so browsers (and headless-browser
render tools) display the content in-page — PDFs render in the browser,
images show inline, etc.

- **Response headers** include `Content-Disposition: inline` and
  `Content-Length`.

**Response** `200` — binary stream

**Errors:** `404` (file not found), `500` (storage failure)

---

### `GET /files/{file_id}/metadata`

Return the full metadata record for a stored file, including enrichment
fields (category, tags, summary, source, and the `metadata_source`
provenance marker) after the AI pipeline completes.

**Response** `200` — [`FileMetadataResponse`](#filemetadataresponse)

**Errors:** `404`

---

### `PATCH /files/{file_id}/metadata`

Set or overwrite a single file's **curated** enrichment metadata directly,
bypassing the automatic AI pipeline.  Useful when an agent or operator
knows the correct values (or needs to correct/fill fields the model left
null or wrong).

**Request body** — any subset of the fields below.  Omitted fields are
left unchanged; an explicit `null` clears a field.

| Field | Type | Description |
|---|---|---|
| `summary` | string \| null | Curated summary |
| `category` | string \| null | Curated category |
| `tags` | string[] \| null | Curated tags (ordered, max 10, stored comma-separated) |
| `metadata_source` | `"agent"` \| `"manual"` | Provenance of the curated values; defaults to `"manual"` |

**Example**

```json
{
  "summary": "Q4 board report",
  "category": "legal",
  "tags": ["report", "2024"],
  "metadata_source": "manual"
}
```

**Response** `200` — [`FileMetadataResponse`](#filemetadataresponse), with
`metadata_source` reflecting the provenance.

**Errors:** `400` (no data fields provided, or only a `metadata_source`
with no data change), `422` (invalid body shape — e.g. a non-string
`summary` or empty `tags` entry, per FastAPI's standard validation
semantics), `404` (file not found)

> Curated values are protected: a later automatic enrichment/reindex pass
> will **not** overwrite them (see `POST /files/reindex` and its `force`
> parameter).

---

### `DELETE /files/{file_id}`

Delete a file and its stored data (both the database record and the
underlying storage blob / object).  The deletion is permanent; the file
cannot be recovered.

**Response** `204` — No Content

**Errors:** `404` (file not found), `500` (storage or database failure)

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

> **Warning:** a single reindex request enqueues at most
> `REINDEX_BATCH_SIZE` (20) files.  This guard prevents an accidental
> full-hub reindex from hammering the enrichment queue.  To reindex more
> than 20 files, repeat the request — use the optional filters below (or
> `GET /files/reindex/progress`) to work through the backlog in batches.

**Query parameters**

| Param | Type | Description |
|---|---|---|
| `category` | string | Only reindex files with this category |
| `content_type` | string | Only reindex files with this MIME type |
| `file_ids` | string | Comma-separated file IDs to reindex |
| `enrichment_status` | string | Filter by status; `empty` selects only files never enriched |
| `force` | bool | When `true`, overwrite agent/manual-curated metadata fields. By default curated records are left untouched |

> By default, records whose metadata was curated by an agent or operator
> (`metadata_source` is `agent`/`manual`) are **skipped** by a reindex so
> their values are never silently clobbered.  Pass `force=true` to
> deliberately overwrite them.

**Response** `200`

```json
{"enqueued": 20, "task_id": "uuid"}
```

The `enqueued` count is capped at 20 per request.  A request matching
no files returns `{"enqueued": 0, "task_id": "uuid"}`.

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
| `deduplicated` | bool | `true` when the response reuses a pre-existing record (same checksum) instead of storing a new copy; always `false` when `allow_duplicate=true` |

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
| `metadata_source` | string \| null | Provenance of the enrichment fields: `"enrichment"` (written by the automatic pipeline) or `"agent"`/`"manual"` (written via `PATCH /files/{id}/metadata`) |

### `FileListResponse`

| Field | Type | Description |
|---|---|---|
| `files` | `FileMetadataResponse[]` | Result page |
| `total` | int | Total matching files |
| `offset` | int | Current offset |
| `limit` | int | Current page size |

### `CategoriesResponse`

| Field | Type | Description |
|---|---|---|
| `categories` | `string[]` | Sorted list of distinct category names across all files |

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
