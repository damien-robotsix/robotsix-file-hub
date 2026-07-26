import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { listFiles, type FileMetadata, type ListFilesParams } from "../api.ts";

const PAGE_SIZE = 20;

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

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
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

  // Filters
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [contentType, setContentType] = useState("");
  const [after, setAfter] = useState("");
  const [before, setBefore] = useState("");

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

  return (
    <div className="files-page">
      <h1>Files</h1>

      {/* Filters */}
      <div className="files-filters">
        <label>
          Category
          <input
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
              setOffset(0);
            }}
            placeholder="e.g. document, image"
          />
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
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.id}>
                <td>{contentTypeIcon(f.content_type)}</td>
                <td>
                  <Link to={`/files/${f.id}`}>{f.filename}</Link>
                </td>
                <td className="cell-mono">{f.content_type}</td>
                <td>{formatBytes(f.size)}</td>
                <td>{formatDate(f.created_at)}</td>
                <td>{f.category ?? "—"}</td>
                <td>{f.tags ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {!loading && files.length === 0 && !error && (
        <p className="files-empty">No files found.</p>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="files-pagination">
          <button
            disabled={currentPage <= 1}
            onClick={() => goToPage(currentPage - 1)}
          >
            ← Prev
          </button>
          <span>
            Page {currentPage} of {totalPages} ({total} files)
          </span>
          <button
            disabled={currentPage >= totalPages}
            onClick={() => goToPage(currentPage + 1)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}
