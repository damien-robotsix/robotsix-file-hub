import { useState, useEffect } from "react";
import { deleteFile, getFileMetadata, type FileMetadata } from "../api.ts";

export function useFileDetail(
  fileId: string | undefined,
  onAfterDelete: () => void,
) {
  const [metadata, setMetadata] = useState<FileMetadata | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!fileId || !metadata) return;
    if (!window.confirm(`Delete "${metadata.filename}"? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await deleteFile(fileId);
      onAfterDelete();
    } catch (e: unknown) {
      setError(String(e));
      setDeleting(false);
    }
  };

  useEffect(() => {
    if (!fileId) return;
    setError(null);
    getFileMetadata(fileId)
      .then(setMetadata)
      .catch((e: unknown) => setError(String(e)));
  }, [fileId]);

  return { metadata, error, setError, deleting, handleDelete };
}
