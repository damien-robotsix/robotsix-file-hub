import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { getFileMetadata, downloadFileUrl, type FileMetadata } from "../api.ts";

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

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

export default function FileDetailPage() {
  const { fileId } = useParams<{ fileId: string }>();
  const [metadata, setMetadata] = useState<FileMetadata | null>(null);
  const [textContent, setTextContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!fileId) return;
    setTextContent(null);
    getFileMetadata(fileId)
      .then(setMetadata)
      .catch((e: unknown) => setError(String(e)));
  }, [fileId]);

  useEffect(() => {
    if (!fileId || !metadata) return;
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
    return () => { cancelled = true; };
  }, [fileId, metadata]);

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

  const previewKind = classifyPreview(metadata.content_type);
  const fileUrl = downloadFileUrl(metadata.id);

  return (
    <div className="file-detail">
      <Link to="/" className="back-link">
        ← Back to Home
      </Link>

      <div className="detail-header">
        <h1>{metadata.filename}</h1>
        <a href={fileUrl} className="download-btn" download={metadata.filename}>
          ⬇ Download
        </a>
      </div>

      <div className="detail-meta">
        <span>
          <strong>Type:</strong> {metadata.content_type}
        </span>
        <span>
          <strong>Size:</strong> {formatSize(metadata.size)}
        </span>
        <span>
          <strong>Uploaded:</strong>{" "}
          {new Date(metadata.created_at).toLocaleString()}
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
          <strong>Checksum:</strong>{" "}
          <code className="checksum">{metadata.checksum}</code>
        </span>
      </div>

      <div className="detail-preview">
        <h2>Preview</h2>
        {previewKind === "image" && (
          <div className="preview-image">
            <img src={fileUrl} alt={metadata.filename} />
          </div>
        )}

        {previewKind === "pdf" && (
          <div className="preview-pdf">
            <object
              data={fileUrl}
              type="application/pdf"
              width="100%"
              height="700"
            >
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
            <p>
              Preview not available for this file type ({metadata.content_type}).
            </p>
            <a
              href={fileUrl}
              className="download-btn"
              download={metadata.filename}
            >
              ⬇ Download {metadata.filename}
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
