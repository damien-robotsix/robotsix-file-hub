import { useEffect, useState } from "react";
import { downloadFileUrl, viewFileUrl } from "../api.ts";
import { formatSize } from "../lib/format.ts";
import { classifyPreview, escapeHtml } from "../lib/preview.ts";
import { useFileDetail } from "../hooks/useFileDetail.ts";

interface FilePreviewProps {
  fileId: string;
  onClose?: () => void;
  showHeader?: boolean;
  showMeta?: boolean;
}

export default function FilePreview({
  fileId,
  onClose,
  showHeader = true,
  showMeta = true,
}: FilePreviewProps) {
  const [textContent, setTextContent] = useState<string | null>(null);
  const { metadata, error, setError, deleting, handleDelete } = useFileDetail(fileId, () => onClose?.());

  useEffect(() => {
    // Reset derived state when the file changes.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTextContent(null);
  }, [fileId]);

  useEffect(() => {
    if (!metadata) return;
    if (classifyPreview(metadata.content_type) !== "text") {
      // Reset text content when preview is not text.
      // eslint-disable-next-line react-hooks/set-state-in-effect
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
        {showHeader && (
          <div className="file-preview-header">
            <h3 className="file-preview-filename">Error loading file</h3>
            {onClose && (
              <div className="file-preview-actions">
                <button onClick={onClose} className="file-preview-close" type="button">
                  ✕ Close
                </button>
              </div>
            )}
          </div>
        )}
        <p className="file-preview-error">{error}</p>
      </div>
    );
  }

  if (!metadata) {
    return (
      <div className="file-preview-panel">
        {showHeader && (
          <div className="file-preview-header">
            <h3 className="file-preview-filename">Loading...</h3>
            {onClose && (
              <div className="file-preview-actions">
                <button onClick={onClose} className="file-preview-close" type="button">
                  ✕ Close
                </button>
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  const previewKind = classifyPreview(metadata.content_type);
  const previewUrl = viewFileUrl(metadata.id);
  const downloadUrl = downloadFileUrl(metadata.id);

  return (
    <div className="file-preview-panel">
      {showHeader && (
        <div className="file-preview-header">
          <h3 className="file-preview-filename">{metadata.filename}</h3>
          <div className="file-preview-actions">
            <a href={downloadUrl} className="download-btn" download={metadata.filename}>
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
            {onClose && (
              <button onClick={onClose} className="file-preview-close" type="button">
                ✕ Close
              </button>
            )}
          </div>
        </div>
      )}

      {showMeta && (
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
      )}

      <div className="file-preview-content">
        {previewKind === "image" && (
          <div className="preview-image">
            <img src={previewUrl} alt={metadata.filename} />
          </div>
        )}

        {previewKind === "pdf" && (
          <div className="preview-pdf">
            <object data={previewUrl} type="application/pdf" width="100%" height="600">
              <p>
                Your browser cannot display PDFs inline.{" "}
                <a href={downloadUrl} download={metadata.filename}>
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
            <div className="fallback-icon" aria-hidden="true">📄</div>
            <p>Preview not available for this file type ({metadata.content_type}).</p>
            <a href={downloadUrl} className="download-btn" download={metadata.filename}>
              ⬇ Download {metadata.filename}
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
