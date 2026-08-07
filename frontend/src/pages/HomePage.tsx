import { useCallback, useEffect, useState } from "react";
import { healthCheck, listFiles, type FileMetadata } from "../api.ts";
import UploadButton from "../components/UploadButton.tsx";
import UploadDialog from "../components/UploadDialog.tsx";
import { formatSize } from "../lib/format.ts";

export default function HomePage() {
  const [status, setStatus] = useState<string | null>(null);
  const [files, setFiles] = useState<FileMetadata[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);

  const refreshFiles = useCallback(() => {
    listFiles({ limit: 20 })
      .then((res) => setFiles(res.files))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  useEffect(() => {
    healthCheck()
      .then((res) => setStatus(res.status))
      .catch((e: unknown) => setError(String(e)));
    refreshFiles();
  }, [refreshFiles]);

  return (
    <div>
      <h1>Robotsix File Hub</h1>
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      <p>Backend status: {status ? <strong>{status}</strong> : "checking..."}</p>

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "0.5rem",
        }}
      >
        <h2 style={{ margin: 0 }}>Recent Files</h2>
        <UploadButton onClick={() => setUploadOpen(true)} />
      </div>

      {files.length === 0 && <p>No files uploaded yet.</p>}
      <ul>
        {files.map((f) => (
          <li key={f.id}>
            {f.filename} ({formatSize(f.size)})
          </li>
        ))}
      </ul>

      <UploadDialog
        open={uploadOpen}
        onClose={() => setUploadOpen(false)}
        onUploadComplete={refreshFiles}
      />
    </div>
  );
}
