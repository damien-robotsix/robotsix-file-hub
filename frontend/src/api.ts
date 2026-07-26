const API_BASE = "/api";

export interface FileMetadata {
  id: string;
  filename: string;
  content_type: string | null;
  size_bytes: number;
  created_at: string;
  embedding: number[] | null;
}

export interface FileListResponse {
  files: FileMetadata[];
  total: number;
}

export interface SearchResult {
  file_id: string;
  filename: string;
  score: number;
  snippet: string | null;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
}

export interface ReindexProgress {
  status: string;
  processed: number;
  total: number;
}

export interface UploadResponse {
  file: FileMetadata;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const res = await fetch(url, options);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  if (res.status === 204) {
    return undefined as unknown as T;
  }
  return res.json() as Promise<T>;
}

export async function uploadFile(file: File): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  return request<UploadResponse>("/", { method: "POST", body: form });
}

export async function uploadFiles(files: File[]): Promise<UploadResponse> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  return request<UploadResponse>("/batch", { method: "POST", body: form });
}

export async function listFiles(params?: {
  skip?: number;
  limit?: number;
}): Promise<FileListResponse> {
  const query = new URLSearchParams();
  if (params?.skip !== undefined) query.set("skip", String(params.skip));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  const qs = query.toString();
  return request<FileListResponse>(`/${qs ? "?" + qs : ""}`);
}

export async function getFileMetadata(fileId: string): Promise<FileMetadata> {
  return request<FileMetadata>(`/${fileId}/metadata`);
}

export async function downloadFile(fileId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/${fileId}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.blob();
}

export async function search(query: string, params?: {
  limit?: number;
  threshold?: number;
}): Promise<SearchResponse> {
  return request<SearchResponse>("/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      ...(params?.limit !== undefined && { limit: params.limit }),
      ...(params?.threshold !== undefined && { threshold: params.threshold }),
    }),
  });
}

export async function triggerReindex(params?: {
  file_ids?: string[];
}): Promise<{ task_id: string; status: string }> {
  return request("/reindex", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params ?? {}),
  });
}

export async function getReindexProgress(): Promise<ReindexProgress> {
  return request<ReindexProgress>("/reindex/progress");
}

export async function healthCheck(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
