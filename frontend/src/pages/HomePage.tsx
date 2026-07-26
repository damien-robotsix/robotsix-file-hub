import { useEffect, useState } from "react";
import { healthCheck, listFiles, type FileMetadata } from "../api.ts";

export default function HomePage() {
  const [status, setStatus] = useState<string | null>(null);
  const [files, setFiles] = useState<FileMetadata[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    healthCheck()
      .then((res) => setStatus(res.status))
      .catch((e: unknown) => setError(String(e)));

    listFiles({ limit: 20 })
      .then((res) => setFiles(res.files))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  return (
    <div>
      <h1>Robotsix File Hub</h1>
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      <p>
        Backend status:{" "}
        {status ? <strong>{status}</strong> : "checking..."}
      </p>
      <h2>Recent Files</h2>
      {files.length === 0 && <p>No files uploaded yet.</p>}
      <ul>
        {files.map((f) => (
          <li key={f.id}>
            {f.filename} ({(f.size_bytes / 1024).toFixed(1)} KB)
          </li>
        ))}
      </ul>
    </div>
  );
}
