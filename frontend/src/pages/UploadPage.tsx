import { useState, type FormEvent } from "react";
import { uploadFile, type UploadResponse } from "../api.ts";

export default function UploadPage() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<UploadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const res = await uploadFile(file);
      setResult(res);
    } catch (err: unknown) {
      setError(String(err));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div>
      <h1>Upload File</h1>
      <form onSubmit={handleSubmit}>
        <input
          type="file"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        />
        <button type="submit" disabled={!file || uploading}>
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </form>
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      {result && (
        <p>
          Uploaded: <strong>{result.file.filename}</strong>{" "}
          ({(result.file.size_bytes / 1024).toFixed(1)} KB)
        </p>
      )}
    </div>
  );
}
