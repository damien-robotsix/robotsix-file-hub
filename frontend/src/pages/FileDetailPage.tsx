import { useParams, Link, useNavigate } from "react-router-dom";
import { downloadFileUrl } from "../api.ts";
import { formatSize } from "../lib/format.ts";
import { useFileDetail } from "../hooks/useFileDetail.ts";
import FilePreview from "../components/FilePreview.tsx";

export default function FileDetailPage() {
  const { fileId } = useParams<{ fileId: string }>();
  const navigate = useNavigate();
  const { metadata, error, deleting, handleDelete } = useFileDetail(fileId, () => navigate("/", { replace: true }));

  if (error) {
    return (
      <div className="file-detail">
        <p className="detail-error">Error: {error}</p>
        <Link to="/" className="back-link">
          ← Back to Home
        </Link>
      </div>
    );
  }

  if (!metadata) {
    return (
      <div className="file-detail">
        <p>Loading...</p>
      </div>
    );
  }

  const fileUrl = downloadFileUrl(metadata.id);

  return (
    <div className="file-detail">
      <Link to="/" className="back-link">
        ← Back to Home
      </Link>

      <div className="detail-header">
        <h1>{metadata.filename}</h1>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
          <a href={fileUrl} className="download-btn" download={metadata.filename}>
            ⬇ Download
          </a>
          <button
            className="delete-btn"
            onClick={handleDelete}
            disabled={deleting}
            type="button"
          >
            {deleting ? "Deleting…" : "🗑 Delete"}
          </button>
        </div>
      </div>

      <div className="detail-meta">
        <span>
          <strong>Type:</strong> {metadata.content_type}
        </span>
        <span>
          <strong>Size:</strong> {formatSize(metadata.size)}
        </span>
        <span>
          <strong>Uploaded:</strong> {new Date(metadata.created_at).toLocaleString()}
        </span>
        {metadata.category && (
          <span>
            <strong>Category:</strong> {metadata.category}
          </span>
        )}
        {metadata.tags && (
          <span>
            <strong>Tags:</strong> {metadata.tags}
          </span>
        )}
        {metadata.summary && (
          <span>
            <strong>Summary:</strong> {metadata.summary}
          </span>
        )}
        <span>
          <strong>Checksum:</strong> <code className="checksum">{metadata.checksum}</code>
        </span>
      </div>

      <div className="detail-preview">
        <h2>Preview</h2>
        <FilePreview fileId={fileId!} showHeader={false} showMeta={false} />
      </div>
    </div>
  );
}
