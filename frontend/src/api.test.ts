import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  listFiles,
  getFileMetadata,
  search,
  uploadFile,
  healthCheck,
  deleteFile,
  downloadFileUrl,
  uploadFilesBatchWithProgress,
  type FileListResponse,
  type FileMetadata,
  type SearchResponse,
} from "./api";
import { TOKEN_KEY } from "./tokenStorage";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "Error",
    text: () => Promise.resolve(JSON.stringify(body)),
    json: () => Promise.resolve(body),
  });
}

const fakeFile: FileMetadata = {
  id: "abc-123", filename: "test.txt", size: 42, content_type: "text/plain",
  checksum: "sha256:deadbeef", storage_key: "sk-1", created_at: "2025-01-15T10:00:00Z",
  category: null, tags: null, summary: null, source: null,
};

describe("API client", () => {
  beforeEach(() => { localStorage.clear(); });

  it("attaches Authorization header when token is present", async () => {
    localStorage.setItem(TOKEN_KEY, "my-jwt");
    const f = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", f);
    await listFiles();
    const [, init] = f.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer my-jwt");
  });

  it("omits Authorization header when no token", async () => {
    const f = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", f);
    await listFiles();
    const [, init] = f.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });

  it("still works when localStorage throws", async () => {
    const orig = localStorage.getItem;
    localStorage.getItem = () => { throw new Error("denied"); };
    const f = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", f);
    try { await listFiles(); } finally { localStorage.getItem = orig; }
  });

  it("throws on non-OK HTTP status", async () => {
    const f = vi.fn().mockResolvedValue({ ok: false, status: 500, statusText: "ERR", text: () => Promise.resolve("boom") });
    vi.stubGlobal("fetch", f);
    await expect(listFiles()).rejects.toThrow("500 ERR: boom");
  });

  it("returns undefined for 204 No Content", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, status: 204, statusText: "NC", text: () => Promise.resolve("") });
    vi.stubGlobal("fetch", f);
    expect(await deleteFile("x")).toBeUndefined();
  });

  it("listFiles builds query string", async () => {
    const f = mockFetch(200, { files: [fakeFile], total: 1, offset: 10, limit: 20 });
    vi.stubGlobal("fetch", f);
    await listFiles({ offset: 10, limit: 20, category: "r", tag: "u" });
    const [url] = f.mock.calls[0] as [string];
    expect(url).toContain("offset=10");
  });

  it("listFiles omits query string when no params", async () => {
    const f = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", f);
    await listFiles();
    expect((f.mock.calls[0] as [string])[0]).toBe("/api/files");
  });

  it("getFileMetadata returns metadata", async () => {
    const f = mockFetch(200, fakeFile);
    vi.stubGlobal("fetch", f);
    expect(await getFileMetadata("abc-123")).toEqual(fakeFile);
  });

  it("search posts JSON", async () => {
    const res: SearchResponse = { results: [], total: 0, offset: 0, limit: 50, query: "hi" };
    const f = mockFetch(200, res);
    vi.stubGlobal("fetch", f);
    const [, init] = f.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
  });

  it("uploadFile sends multipart form data", async () => {
    const f = mockFetch(200, fakeFile);
    vi.stubGlobal("fetch", f);
    const file = new File(["c"], "t.txt", { type: "text/plain" });
    await uploadFile(file);
    expect((f.mock.calls[0] as [string, RequestInit])[1].method).toBe("POST");
  });

  it("healthCheck returns status", async () => {
    const f = mockFetch(200, { status: "ok" });
    vi.stubGlobal("fetch", f);
    expect((await healthCheck()).status).toBe("ok");
  });

  it("downloadFileUrl returns correct URL", () => {
    expect(downloadFileUrl("abc-123")).toBe("/api/files/abc-123");
  });

  it("deleteFile sends DELETE with X-Confirm-Delete", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, status: 204, statusText: "NC", text: () => Promise.resolve("") });
    vi.stubGlobal("fetch", f);
    await deleteFile("x");
    const [, init] = f.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-Confirm-Delete"]).toBe("true");
  });
});

// -- batch upload tests --------------------------------------------------

interface MockXHR {
  upload: { addEventListener: ReturnType<typeof vi.fn> };
  addEventListener: ReturnType<typeof vi.fn>;
  open: ReturnType<typeof vi.fn>;
  setRequestHeader: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
  status: number; statusText: string; responseText: string;
  _triggerUploadEvent(e: string, d?: Partial<ProgressEvent>): void;
  _triggerEvent(e: string, d?: Partial<ProgressEvent>): void;
}

function createMockXHR(o?: Partial<MockXHR>): MockXHR {
  const ul = new Map<string, Array<(e: unknown) => void>>();
  const ls = new Map<string, Array<(e: unknown) => void>>();
  return {
    upload: { addEventListener: vi.fn((ev: string, h: (d: unknown) => void) => { const a = ul.get(ev) ?? []; a.push(h); ul.set(ev, a); }) },
    addEventListener: vi.fn((ev: string, h: (d: unknown) => void) => { const a = ls.get(ev) ?? []; a.push(h); ls.set(ev, a); }),
    open: vi.fn(), setRequestHeader: vi.fn(), send: vi.fn(),
    status: 200, statusText: "OK", responseText: "",
    _triggerUploadEvent(ev: string, d?: Partial<ProgressEvent>) { for (const h of ul.get(ev) ?? []) h(d ?? {}); },
    _triggerEvent(ev: string, d?: Partial<ProgressEvent>) { for (const h of ls.get(ev) ?? []) h(d ?? {}); },
    ...o,
  };
}

describe("uploadFilesBatchWithProgress", () => {
  let mx: MockXHR;
  beforeEach(() => { localStorage.clear(); mx = createMockXHR(); vi.stubGlobal("XMLHttpRequest", vi.fn(() => mx)); });
  function mf(n: string, s: number) { return new File([new Uint8Array(s)], n); }

  it("resolves with FileMetadata[] on 2xx", async () => {
    const p = uploadFilesBatchWithProgress([mf("a", 100)], vi.fn());
    mx.responseText = JSON.stringify({ files: [{ id:"1", filename:"a", size:100, content_type:"t", checksum:"c", storage_key:"s", created_at:"d" }] });
    mx._triggerEvent("load");
    expect(await p).toHaveLength(1);
  });

  it("falls back content_type to application/octet-stream", async () => {
    const p = uploadFilesBatchWithProgress([mf("x", 10)], vi.fn());
    mx.responseText = JSON.stringify({ files: [{ id:"x", filename:"x", size:10, checksum:"c", storage_key:"s", created_at:"t" }] });
    mx._triggerEvent("load");
    expect((await p)[0].content_type).toBe("application/octet-stream");
  });

  it("uses empty array when files missing", async () => {
    const p = uploadFilesBatchWithProgress([mf("x", 10)], vi.fn());
    mx.responseText = "{}";
    mx._triggerEvent("load");
    expect(await p).toEqual([]);
  });

  it("calls onFileProgress with cumulative offsets", () => {
    const calls: Array<{ i: number; p: number }> = [];
    uploadFilesBatchWithProgress([mf("a", 100), mf("b", 200)], (i, p) => calls.push({ i, p }));
    mx._triggerUploadEvent("progress", { lengthComputable: true, loaded: 150 } as ProgressEvent);
    expect(calls).toEqual([{ i: 0, p: 1 }, { i: 1, p: 0.25 }]);
  });

  it("skips progress when !lengthComputable", () => {
    const cb = vi.fn();
    uploadFilesBatchWithProgress([mf("a", 100)], cb);
    mx._triggerUploadEvent("progress", { lengthComputable: false } as ProgressEvent);
    expect(cb).not.toHaveBeenCalled();
  });

  it("skips progress when all zero-byte files", () => {
    const cb = vi.fn();
    uploadFilesBatchWithProgress([mf("e", 0)], cb);
    mx._triggerUploadEvent("progress", { lengthComputable: true, loaded: 10 } as ProgressEvent);
    expect(cb).not.toHaveBeenCalled();
  });

  it("attaches Authorization header", () => {
    localStorage.setItem(TOKEN_KEY, "jwt");
    uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    expect(mx.setRequestHeader).toHaveBeenCalledWith("Authorization", "Bearer jwt");
  });

  it("omits Authorization header when no token", () => {
    uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    expect(mx.setRequestHeader).not.toHaveBeenCalled();
  });

  it("survives localStorage throws", () => {
    const orig = localStorage.getItem;
    localStorage.getItem = () => { throw new Error("denied"); };
    try { uploadFilesBatchWithProgress([mf("a", 10)], vi.fn()); } finally { localStorage.getItem = orig; }
  });

  it("rejects on non-2xx", async () => {
    mx.status = 500; mx.statusText = "ERR"; mx.responseText = "boom";
    const p = uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    mx._triggerEvent("load");
    await expect(p).rejects.toThrow("500 ERR: boom");
  });

  it("rejects on network error", async () => {
    const p = uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    mx._triggerEvent("error");
    await expect(p).rejects.toThrow("Network error during upload");
  });

  it("rejects on abort", async () => {
    const p = uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    mx._triggerEvent("abort");
    await expect(p).rejects.toThrow("Upload aborted");
  });

  it("rejects on invalid JSON", async () => {
    mx.responseText = "bad";
    const p = uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    mx._triggerEvent("load");
    await expect(p).rejects.toThrow("Invalid JSON response");
  });

  it("opens POST to /api/files/batch", () => {
    uploadFilesBatchWithProgress([mf("a", 10)], vi.fn());
    expect(mx.open).toHaveBeenCalledWith("POST", "/api/files/batch");
  });

  it("sends FormData with files", () => {
    uploadFilesBatchWithProgress([mf("a.txt", 10), mf("b.txt", 20)], vi.fn());
    expect(mx.send).toHaveBeenCalledTimes(1);
    expect((mx.send.mock.calls[0][0] as FormData).getAll("files")).toHaveLength(2);
  });
});
