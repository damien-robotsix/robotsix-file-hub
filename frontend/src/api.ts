import { TOKEN_KEY } from "./AuthContext.tsx";

const API_BASE = "/api";

function getAuthHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  try {
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  } catch {
    // localStorage unavailable (SSR / test), skip
  }
  return headers;
}

export interface FileMetadata {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  checksum: string;
  storage_path: string;
  created_at: string;
  category: string | null;
  tags: string | null;
  summary: string | null;
  source: string | null;
}

export interface FileListResponse {
  files: FileMetadata[];
  total: number;
  offset: number;
  limit: number;
}

export interface SearchResult {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  checksum: string;
  created_at: string;
  category: string | null;
  tags: string | null;
  summary: string | null;
  source: string | null;
  relevance: number;
}

export interface SearchResponse {
  results: SearchResult[];
  total: number;
  offset: number;
  limit: number;
  query: string;
}

export interface ReindexProgress {
  total: number;
  completed: number;
  failed: number;
  active: boolean;
}

export type UploadResponse = FileMetadata;

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}${path}`;
  const authHeaders = getAuthHeaders();

  const merged: RequestInit = {
    ...options,
    headers: {
      ...authHeaders,
      ...(options?.headers as Record<string, string> | undefined),
    },
  };

  const res = await fetch(url, merged);
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
  return request<UploadResponse>("/files", { method: "POST", body: form });
}

export async function uploadFiles(files: File[]): Promise<{ files: FileMetadata[] }> {
  const form = new FormData();
  for (const f of files) {
    form.append("files", f);
  }
  return request<{ files: FileMetadata[] }>("/files/batch", { method: "POST", body: form });
}

export interface ListFilesParams {
  offset?: number;
  limit?: number;
  category?: string;
  tag?: string;
  content_type?: string;
  source?: string;
  before?: string;
  after?: string;
}

export async function listFiles(params?: ListFilesParams): Promise<FileListResponse> {
  const query = new URLSearchParams();
  if (params?.offset !== undefined) query.set("offset", String(params.offset));
  if (params?.limit !== undefined) query.set("limit", String(params.limit));
  if (params?.category !== undefined) query.set("category", params.category);
  if (params?.tag !== undefined) query.set("tag", params.tag);
  if (params?.content_type !== undefined) query.set("content_type", params.content_type);
  if (params?.source !== undefined) query.set("source", params.source);
  if (params?.before !== undefined) query.set("before", params.before);
  if (params?.after !== undefined) query.set("after", params.after);
  const qs = query.toString();
  return request<FileListResponse>(`/files${qs ? "?" + qs : ""}`);
}

export async function getFileMetadata(fileId: string): Promise<FileMetadata> {
  return request<FileMetadata>(`/files/${fileId}/metadata`);
}

export function downloadFileUrl(fileId: string): string {
  return `${API_BASE}/files/${fileId}`;
}

export async function downloadFile(fileId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/files/${fileId}`, {
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.blob();
}

export async function search(query: string, params?: {
  limit?: number;
  offset?: number;
}): Promise<SearchResponse> {
  return request<SearchResponse>("/files/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      offset: params?.offset ?? 0,
      limit: params?.limit ?? 50,
    }),
  });
}

export async function triggerReindex(params?: {
  file_ids?: string[];
  category?: string;
  content_type?: string;
}): Promise<{ status: string }> {
  const query = new URLSearchParams();
  if (params?.file_ids?.length) query.set("file_ids", params.file_ids.join(","));
  if (params?.category) query.set("category", params.category);
  if (params?.content_type) query.set("content_type", params.content_type);
  const qs = query.toString();
  return request(`/files/reindex${qs ? "?" + qs : ""}`, { method: "POST" });
}

export async function getReindexProgress(): Promise<ReindexProgress> {
  return request<ReindexProgress>("/files/reindex/progress");
}

export async function healthCheck(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
