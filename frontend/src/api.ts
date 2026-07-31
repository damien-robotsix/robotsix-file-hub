import { TOKEN_KEY } from "./tokenStorage";

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

/**
 * Upload a single file with progress tracking via XMLHttpRequest.
 * Calls `onProgress` with a value between 0 and 1.
 * Returns the FileMetadata for the uploaded file.
 */
export function uploadFileWithProgress(
  file: File,
  onProgress: (progress: number) => void,
): Promise<FileMetadata> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/files`);

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable) {
        onProgress(e.loaded / e.total);
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const raw = JSON.parse(xhr.responseText);
          resolve({
            id: raw.id,
            filename: raw.filename,
            content_type: raw.content_type ?? null,
            size: raw.size,
            created_at: raw.created_at,
          });
        } catch {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        reject(new Error(`${xhr.status} ${xhr.statusText}: ${xhr.responseText}`));
      }
    });

    xhr.addEventListener("error", () => {
      reject(new Error("Network error during upload"));
    });

    xhr.addEventListener("abort", () => {
      reject(new Error("Upload aborted"));
    });

    xhr.send(form);
  });
}

/**
 * Upload multiple files in a single batch POST /files/batch with per-file
 * progress estimation.  Calls `onFileProgress(index, progress)` for each
 * file with a value between 0 and 1, estimated from the overall upload
 * byte progress distributed across files proportionally.
 */
export function uploadFilesBatchWithProgress(
  files: File[],
  onFileProgress: (index: number, progress: number) => void,
): Promise<FileMetadata[]> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    const fileSizes: number[] = [];
    for (const f of files) {
      form.append("files", f);
      fileSizes.push(f.size);
    }

    // Cumulative byte offsets for per-file progress estimation
    const offsets: number[] = [];
    let cum = 0;
    for (const sz of fileSizes) {
      offsets.push(cum);
      cum += sz;
    }
    const totalFileBytes = cum;

    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/files/batch`);

    // Set auth header (XHR does not go through the request() helper)
    try {
      const token = localStorage.getItem(TOKEN_KEY);
      if (token) {
        xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      }
    } catch {
      // localStorage unavailable
    }

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && totalFileBytes > 0) {
        const loaded = e.loaded;
        for (let i = 0; i < files.length; i++) {
          const start = offsets[i];
          const size = fileSizes[i];
          const fileLoaded = Math.max(0, Math.min(size, loaded - start));
          onFileProgress(i, fileLoaded / size);
        }
      }
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const raw = JSON.parse(xhr.responseText);
          const uploaded: FileMetadata[] = (raw.files ?? []).map((f: Record<string, unknown>) => ({
            id: f.id as string,
            filename: f.filename as string,
            size: f.size as number,
            content_type: (f.content_type as string) ?? "application/octet-stream",
            checksum: f.checksum as string,
            storage_path: "",
            created_at: f.created_at as string,
            category: null,
            tags: null,
            summary: null,
            source: null,
          }));
          resolve(uploaded);
        } catch {
          reject(new Error("Invalid JSON response"));
        }
      } else {
        reject(new Error(`${xhr.status} ${xhr.statusText}: ${xhr.responseText}`));
      }
    });

    xhr.addEventListener("error", () => {
      reject(new Error("Network error during upload"));
    });

    xhr.addEventListener("abort", () => {
      reject(new Error("Upload aborted"));
    });

    xhr.send(form);
  });
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

export interface CategoriesResponse {
  categories: string[];
}

export async function listCategories(): Promise<CategoriesResponse> {
  return request<CategoriesResponse>("/files/categories");
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

export async function search(
  query: string,
  params?: {
    limit?: number;
    offset?: number;
  },
): Promise<SearchResponse> {
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

// ---------------------------------------------------------------------------
// Auth helpers
// ---------------------------------------------------------------------------

export function setAuthToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // localStorage unavailable (SSR / test), skip
  }
}

export function clearAuthToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // localStorage unavailable (SSR / test), skip
  }
}

// ---------------------------------------------------------------------------
// Delete
// ---------------------------------------------------------------------------

export async function deleteFile(fileId: string): Promise<void> {
  await request<void>(`/files/${fileId}`, {
    method: "DELETE",
    headers: { "X-Confirm-Delete": "true" },
  });
}
