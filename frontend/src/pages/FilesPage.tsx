import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  deleteFile,
  listFiles,
  listCategories,
  triggerReindex,
  getReindexProgress,
  type FileMetadata,
  type ListFilesParams,
  type ReindexProgress,
} from "../api.ts";
import FilePreview from "../components/FilePreview.tsx";
import UploadButton from "../components/UploadButton.tsx";
import UploadDialog from "../components/UploadDialog.tsx";
import { formatSize } from "../lib/format.ts";

const PAGE_SIZE = 20;
const MAX_PAGE_BUTTONS = 7;

const CONTENT_TYPE_ICONS: Record<string, string> = {
  "application/pdf": "📄",
  "application/msword": "📝",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "📝",
  "application/vnd.ms-excel": "📊",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "📊",
  "application/zip": "📦",
  "application/gzip": "📦",
  "application/x-tar": "📦",
  "text/plain": "📃",
  "text/csv": "📃",
  "text/html": "🌐",
  "text/markdown": "📃",
  "image/png": "🖼️",
  "image/jpeg": "🖼️",
  "image/gif": "🖼️",
  "image/svg+xml": "🖼️",
  "audio/mpeg": "🎵",
  "audio/wav": "🎵",
  "video/mp4": "🎬",
};

function contentTypeIcon(contentType: string): string {
  const key = contentType.split(";")[0].trim();
  return CONTENT_TYPE_ICONS[key] ?? "📁";
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString();
}

function coerceDate(iso: string | undefined): string {
  if (!iso) return "";
  // convert datetime-local value to ISO string the backend expects
  try {
    return new Date(iso).toISOString();
  } catch {
    return "";
  }
}

export default function FilesPage() {
  const [files, setFiles] = useState<FileMetadata[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  // Filters
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [contentType, setContentType] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");

  // Reindex
  const [reindexing, setReindexing] = useState(false);
  const [reindexProgress, setReindexProgress] = useState<ReindexProgress | null>(null);
  const [reindexError, setReindexError] = useState<string | null>(null);
  const reindexTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Available categories for dropdown
  const [categories, setCategories] = useState<string[]>([]);

  useEffect(() => {
    listCategories()
      .then((res) => setCategories(res.categories))
      .catch(() => setCategories([]));
  }, []);

  const handleDelete = async (fileId: string, filename: string) => {
    if (!window.confirm(`Delete "${filename}"? This cannot be undone.`)) return;
    setError(null);
    try {
      await deleteFile(fileId);
      await fetchFiles();
    } catch (e: unknown) {
      setError(String(e));
    }
  };

  const handleReindex = async () => {
    setReindexError(null);
    setReindexProgress(null);
    setReindexing(true);
    try {
      await triggerReindex();
    } catch (e: unknown) {
      setReindexError(String(e));
      setReindexing(false);
    }
  };

  // Poll reindex progress while active
  useEffect(() => {
    if (!reindexing) return;

    const poll = async () => {
      try {
        const progress = await getReindexProgress();
        setReindexProgress(progress);
        if (!progress.active) {
          setReindexing(false);
        }
      } catch (e: unknown) {
        setReindexError(String(e));
        setReindexing(false);
      }
    };

    poll(); // immediate first poll
    reindexTimerRef.current = setInterval(poll, 2000);

    return () => {
      if (reindexTimerRef.current !== null) {
        clearInterval(reindexTimerRef.current);
        reindexTimerRef.current = null;
      }
    };
  }, [reindexing]);

  const fetchFiles = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: ListFilesParams = {
        offset,
        limit: PAGE_SIZE,
      };
      if (category) params.category = category;
      if (tag) params.tag = tag;
      if (contentType) params.content_type = contentType;
      if (after) params.after = coerceDate(after);
      if (before) params.before = coerceDate(before);

      const res = await listFiles(params);
      setFiles(res.files);
      setTotal(res.total);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [offset, category, tag, contentType, after, before]);

  useEffect(() => {
    fetchFiles();
  }, [fetchFiles]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1;

  function goToPage(page: number) {
    setOffset((page - 1) * PAGE_SIZE);
  }

  function pageNumbers(): (number | "...")[] {
    if (totalPages <= MAX_PAGE_BUTTONS) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    const pages: (number | "...")[] = [];
    const left = Math.max(2, currentPage - 1);
    const right = Math.min(totalPages - 1, currentPage + 1);

    pages.push(1);
    if (left > 2) pages.push("...");
    for (let p = left; p <= right; p++) pages.push(p);
    if (right < totalPages - 1) pages.push("...");
    pages.push(totalPages);
    return pages;
  }

  return (
    <div className="files-page">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <h1>Files</h1>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <button
            onClick={handleReindex}
            disabled={reindexing}
            style={{
              padding: "0.4rem 0.9rem",
              fontSize: "0.9rem",
              cursor: reindexing ? "not-allowed" : "pointer",
              background: "#6c757d",
              color: "#fff",
              border: "none",
              borderRadius: "4px",
              opacity: reindexing ? 0.65 : 1,
            }}
          >
            {reindexing ? "Reindexing…" : "Reindex"}
          </button>
          <UploadButton onClick={() => setUploadOpen(true)} />
        </div>
      </div>

      {/* Filters */}
      <div className="files-filters">
        <label>
          Category
          <select
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setOffset(0);
            }}
          >
            <option value="">All</option>
            {categories.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tag
          <input
            value={tag}
            onChange={(e) => {
              setTag(e.target.value);
              setOffset(0);
            }}
            placeholder="Substring match"
          />
        </label>
        <label>
          Content Type
          <input
            value={contentType}
            onChange={(e) => {
              setContentType(e.target.value);
              setOffset(0);
            }}
            placeholder="e.g. application/pdf"
          />
        </label>
        <label>
          After
          <input
            type="datetime-local"
            value={after}
            onChange={(e) => {
              setAfter(e.target.value);
              setOffset(0);
            }}
          />
        </label>
        <label>
          Before
          <input
            type="datetime-local"
            value={before}
            onChange={(e) => {
              setBefore(e.target.value);
              setOffset(0);
            }}
          />
        </label>
      </div>

      {/* Error */}
      {error && <p className="files-error">{error}</p>}

      {/* Reindex status */}
      {(reindexing || reindexProgress || reindexError) && (
        <div style={{ marginBottom: "1rem" }}>
          {reindexError && <p className="files-error">{reindexError}</p>}
          {reindexProgress && (
            <div style={{ padding: "0.75rem", background: "#f8f9fa", borderRadius: "6px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: "0.4rem" }}>
                <span style={{ fontSize: "0.9rem", fontWeight: 500 }}>
                  {reindexProgress.active ? "Reindexing…" : "Reindex complete"}
                </span>
                <span style={{ fontSize: "0.85rem", color: "#6c757d" }}>
                  {reindexProgress.completed} / {reindexProgress.total}
                  {reindexProgress.failed > 0 && ` (${reindexProgress.failed} failed)`}
                </span>
              </div>
              <div className="upload-progress-bar">
                <div
                  className={`progress-fill${reindexProgress.failed > 0 ? " error" : ""}${!reindexProgress.active && reindexProgress.failed === 0 ? " success" : ""}`}
                  style={{
                    width: `${reindexProgress.total > 0 ? Math.round((reindexProgress.completed / reindexProgress.total) * 100) : 0}%`,
                  }}
                />
              </div>
            </div>
          )}
        </div>
      )}

      {/* Inline file preview */}
      {selectedFileId && (
        <FilePreview fileId={selectedFileId} onClose={() => setSelectedFileId(null)} />
      )}

      {/* Loading */}
      {loading && <p>Loading…</p>}

      {/* Table */}
      {!loading && files.length > 0 && (
        <table className="files-table">
          <thead>
            <tr>
              <th></th>
              <th>Filename</th>
              <th>Type</th>
              <th>Size</th>
              <th>Uploaded</th>
              <th>Category</th>
              <th>Tags</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.id}>
                <td>{contentTypeIcon(f.content_type)}</td>
                <td>
                  <Link
                    to={`/files/${f.id}`}
                    onClick={(e) => {
                      e.preventDefault();
                      setSelectedFileId(f.id);
                    }}
                  >
                    {f.filename}
                  </Link>
                </td>
                <td className="cell-mono">{f.content_type}</td>
                <td>{formatSize(f.size)}</td>
                <td>{formatDate(f.created_at)}</td>
                <td>{f.category ? <span className="badge badge-category">{f.category}</span> : "—"}</td>
                <td>
                  {f.tags
                    ? f.tags.split(",").map((t) => (
                        <span key={t} className="badge badge-tag">
                          {t.trim()}
                        </span>
                      ))
                    : "—"}
                </td>
                <td>
                  <button
                    className="delete-btn"
                    onClick={() => handleDelete(f.id, f.filename)}
                    title="Delete file"
                  >
                    🗑 Delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!loading && files.length === 0 && !error && <p className="files-empty">No files found.</p>}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="files-pagination">
          <button disabled={currentPage <= 1} onClick={() => goToPage(currentPage - 1)}>
            ← Prev
          </button>
          {pageNumbers().map((p, i) =>
            p === "..." ? (
              <span key={`ellipsis-${i}`} className="pagination-ellipsis">
                …
              </span>
            ) : (
              <button
                key={p}
                className={p === currentPage ? "pagination-active" : ""}
                onClick={() => goToPage(p)}
              >
                {p}
              </button>
            ),
          )}
          <button disabled={currentPage >= totalPages} onClick={() => goToPage(currentPage + 1)}>
            Next →
          </button>
        </div>
      )}

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploadComplete={fetchFiles}
      />
    </div>
  );
}
