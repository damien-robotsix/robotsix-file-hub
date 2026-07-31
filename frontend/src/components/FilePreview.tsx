import { useEffect, useState } from "react";
import { deleteFile, getFileMetadata, downloadFileUrl, type FileMetadata } from "../api.ts";
import { formatSize } from "../lib/format.ts";

type PreviewKind = "image" | "pdf" | "text" | "unsupported";

function classifyPreview(contentType: string | null): PreviewKind {
  if (!contentType) return "unsupported";
  if (contentType.startsWith("image/")) return "image";
  if (contentType === "application/pdf") return "pdf";
  if (
    contentType.startsWith("text/") ||
    contentType === "application/json" ||
    contentType === "application/javascript" ||
    contentType === "application/xml"
  ) {
    return "text";
  }
  return "unsupported";
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

interface FilePreviewProps {
  fileId: string;
  onClose: () => void;
}

export default function FilePreview({ fileId, onClose }: FilePreviewProps) {
  const [metadata, setMetadata] = useState<FileMetadata | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!metadata) return;
    if (!window.confirm(`Delete "${metadata.filename}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await deleteFile(fileId);
      onClose();
    } catch (e: unknown) {
      setError(String(e));
      setDeleting(false);
    }
  };

  useEffect(() => {
    setTextContent(null);
    setError(null);
    getFileMetadata(fileId)
      .then(setMetadata)
      .catch((e: unknown) => setError(String(e)));
  }, [fileId]);

  useEffect(() => {
    if (!metadata) return;
    if (classifyPreview(metadata.content_type) !== "text") {
      setTextContent(null);
      return;
    }

    let cancelled = false;
    fetch(downloadFileUrl(fileId))
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.text();
      })
      .then((text) => {
        if (!cancelled) setTextContent(text);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [fileId, metadata]);

  if (error) {
    return (
      <div className="file-preview-panel">
        <div className="file-preview-header">
          <h3 className="file-preview-filename">Error loading file</h3>
          <div className="file-preview-actions">
            <button onClick={onClose} className="file-preview-close" type="button">
              ✕ Close
            </button>
          </div>
        </div>
        <p className="file-preview-error">{error}</p>
      </div>
    );
  }

  if (!metadata) {
    return (
      <div className="file-preview-panel">
        <div className="file-preview-header">
          <h3 className="file-preview-filename">Loading...</h3>
          <div className="file-preview-actions">
            <button onClick={onClose} className="file-preview-close" type="button">
              ✕ Close
            </button>
          </div>
        </div>
      </div>
    );
  }

  const previewKind = classifyPreview(metadata.content_type);
  const fileUrl = downloadFileUrl(metadata.id);

  return (
    <div className="file-preview-panel">
      <div className="file-preview-header">
        <h3 className="file-preview-filename">{metadata.filename}</h3>
        <div className="file-preview-actions">
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
          <button onClick={onClose} className="file-preview-close" type="button">
            ✕ Close
          </button>
        </div>
      </div>

      <div className="file-preview-meta">
        <span>{formatSize(metadata.size)}</span>
        <span>{metadata.content_type}</span>
        {metadata.category && <span className="file-preview-category">{metadata.category}</span>}
        {metadata.tags && (
          <span className="file-preview-tags">
            {metadata.tags.split(",").map((t) => (
              <span key={t} className="tag">
                {t.trim()}
              </span>
            ))}
          </span>
        )}
      </div>

      <div className="file-preview-content">
        {previewKind === "image" && (
          <div className="preview-image">
            <img src={fileUrl} alt={metadata.filename} />
          </div>
        )}

        {previewKind === "pdf" && (
          <div className="preview-pdf">
            <object data={fileUrl} type="application/pdf" width="100%" height="600">
              <p>
                Your browser cannot display PDFs inline.{" "}
                <a href={fileUrl} download={metadata.filename}>
                  Download the PDF
                </a>
                .
              </p>
            </object>
          </div>
        )}

        {previewKind === "text" && (
          <div className="preview-text">
            {textContent === null ? (
              <p>Loading text content...</p>
            ) : (
              <pre
                className="code-block"
                dangerouslySetInnerHTML={{ __html: escapeHtml(textContent) }}
              />
            )}
          </div>
        )}

        {previewKind === "unsupported" && (
          <div className="preview-unsupported">
            <div className="fallback-icon">📄</div>
            <p>Preview not available for this file type ({metadata.content_type}).</p>
            <a href={fileUrl} className="download-btn" download={metadata.filename}>
              ⬇ Download {metadata.filename}
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
