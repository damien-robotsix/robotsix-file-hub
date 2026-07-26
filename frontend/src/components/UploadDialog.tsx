import { useState, useRef, useCallback, type DragEvent, type ChangeEvent } from "react";
import { uploadFileWithProgress, type FileMetadata } from "../api.ts";
import "./UploadDialog.css";

interface UploadDialogProps {
  open: boolean;
  onClose: () => void;
  onUploadComplete: () => void;
}

interface FileEntry {
  file: File;
  id: string;
}

type UploadResult =
  | { kind: "uploading"; progress: number }
  | { kind: "success"; metadata: FileMetadata }
  | { kind: "error"; message: string };

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function UploadDialog({ open, onClose, onUploadComplete }: UploadDialogProps) {
  const [selectedFiles, setSelectedFiles] = useState<FileEntry[]>([]);
  const [results, setResults] = useState<Map<string, UploadResult>>(new Map());
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const nextId = useRef(0);

  const reset = useCallback(() => {
    setSelectedFiles([]);
    setResults(new Map());
    setUploading(false);
    setDragOver(false);
  }, []);

  const handleClose = useCallback(() => {
    if (uploading) return;
    reset();
    onClose();
  }, [uploading, reset, onClose]);

  const addFiles = useCallback((fileList: FileList | null) => {
    if (!fileList) return;
    const entries: FileEntry[] = [];
    for (let i = 0; i < fileList.length; i++) {
      entries.push({ file: fileList[i], id: `f${nextId.current++}` });
    }
    setSelectedFiles((prev) => [...prev, ...entries]);
  }, []);

  const removeFile = useCallback((id: string) => {
    setSelectedFiles((prev) => prev.filter((f) => f.id !== id));
    setResults((prev) => {
      const next = new Map(prev);
      next.delete(id);
      return next;
    });
  }, []);

  const handleDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      if (uploading) return;
      addFiles(e.dataTransfer.files);
    },
    [addFiles, uploading],
  );

  const handleDragOver = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      if (uploading) return;
      setDragOver(true);
    },
    [uploading],
  );

  const handleDragLeave = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      if (uploading) return;
      // Only set dragOver=false when the pointer truly leaves the drop zone,
      // not when it crosses a child element.
      const target = e.currentTarget as HTMLElement;
      const related = e.relatedTarget as HTMLElement | null;
      if (related && target.contains(related)) return;
      setDragOver(false);
    },
    [uploading],
  );

  const handleFileInput = useCallback(
    (e: ChangeEvent<HTMLInputElement>) => {
      addFiles(e.target.files);
      // Reset so the same file can be re-selected
      e.target.value = "";
    },
    [addFiles],
  );

  const startUpload = useCallback(async () => {
    if (selectedFiles.length === 0) return;
    setUploading(true);
    const newResults = new Map<string, UploadResult>();
    for (const entry of selectedFiles) {
      newResults.set(entry.id, { kind: "uploading", progress: 0 });
    }
    setResults(new Map(newResults));

    for (const entry of selectedFiles) {
      try {
        const metadata = await uploadFileWithProgress(entry.file, (progress) => {
          setResults((prev) => {
            const next = new Map(prev);
            next.set(entry.id, { kind: "uploading", progress });
            return next;
          });
        });
        setResults((prev) => {
          const next = new Map(prev);
          next.set(entry.id, { kind: "success", metadata });
          return next;
        });
      } catch (err: unknown) {
        setResults((prev) => {
          const next = new Map(prev);
          next.set(entry.id, { kind: "error", message: String(err) });
          return next;
        });
      }
    }

    setUploading(false);
    onUploadComplete();
  }, [selectedFiles, onUploadComplete]);

  if (!open) return null;

  const allDone =
    !uploading &&
    selectedFiles.length > 0 &&
    selectedFiles.every((f) => {
      const r = results.get(f.id);
      return r && (r.kind === "success" || r.kind === "error");
    });

  return (
    <div className="upload-dialog-overlay" onClick={uploading ? undefined : handleClose}>
      <div className="upload-dialog" onClick={(e) => e.stopPropagation()}>
        <div className="upload-dialog-header">
          <h2>Upload Files</h2>
          <button className="upload-dialog-close" onClick={handleClose} disabled={uploading}>
            &times;
          </button>
        </div>

        <div className="upload-dialog-body">
          {/* Drop zone */}
          <div
            className={`upload-drop-zone ${dragOver ? "drag-over" : ""} ${uploading ? "disabled" : ""}`}
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => {
              if (!uploading) fileInputRef.current?.click();
            }}
          >
            <p>
              Drag &amp; drop files here, or <span className="browse-link">browse</span>
            </p>
            <p style={{ fontSize: "0.8rem" }}>Single or multiple files supported</p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              style={{ display: "none" }}
              onChange={handleFileInput}
            />
          </div>

          {/* Selected file list */}
          {selectedFiles.length > 0 && (
            <ul className="upload-file-list">
              {selectedFiles.map((entry) => {
                const result = results.get(entry.id);
                return (
                  <li key={entry.id}>
                    <div className="upload-file-item">
                      <span className="file-name" title={entry.file.name}>
                        {entry.file.name}
                      </span>
                      <span className="file-size">{formatSize(entry.file.size)}</span>
                      {!uploading && (
                        <button
                          className="remove-btn"
                          onClick={() => removeFile(entry.id)}
                          title="Remove"
                        >
                          &times;
                        </button>
                      )}
                    </div>

                    {/* Progress bar */}
                    {result && result.kind === "uploading" && (
                      <div className="upload-progress-bar">
                        <div
                          className="progress-fill"
                          style={{ width: `${Math.round(result.progress * 100)}%` }}
                        />
                      </div>
                    )}

                    {/* Result status */}
                    {result && result.kind === "success" && (
                      <>
                        <div className="upload-progress-bar">
                          <div className="progress-fill success" style={{ width: "100%" }} />
                        </div>
                        <div className="upload-result-status success">
                          Uploaded &mdash; {formatSize(result.metadata.size_bytes ?? 0)}
                        </div>
                      </>
                    )}
                    {result && result.kind === "error" && (
                      <>
                        <div className="upload-progress-bar">
                          <div className="progress-fill error" style={{ width: "100%" }} />
                        </div>
                        <div className="upload-result-status error">{result.message}</div>
                      </>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>

        <div className="upload-dialog-footer">
          <button onClick={handleClose} disabled={uploading}>
            {allDone ? "Close" : "Cancel"}
          </button>
          <button
            className="primary"
            disabled={selectedFiles.length === 0 || uploading}
            onClick={startUpload}
          >
            {uploading
              ? "Uploading..."
              : `Upload ${selectedFiles.length > 0 ? `(${selectedFiles.length})` : ""}`}
          </button>
        </div>
      </div>
    </div>
  );
}
