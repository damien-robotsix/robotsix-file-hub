import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  listFiles,
  getFileMetadata,
  search,
  uploadFile,
  healthCheck,
  deleteFile,
  downloadFileUrl,
  listCategories,
  triggerReindex,
  getReindexProgress,
  uploadFilesBatchWithProgress,
  type FileMetadata,
  type SearchResponse,
  type CategoriesResponse,
  type ReindexProgress,
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
    await search("hi");
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

  // -- listCategories -------------------------------------------------------

  it("listCategories returns the categories array", async () => {
    const resp: CategoriesResponse = { categories: ["reports", "images"] };
    const fetch = mockFetch(200, resp);
    vi.stubGlobal("fetch", fetch);

    const result = await listCategories();
    expect(result).toEqual(resp);
    expect(fetch).toHaveBeenCalledWith("/api/files/categories", expect.anything());
  });

  // -- triggerReindex -------------------------------------------------------

  it("triggerReindex posts to the reindex endpoint", async () => {
    const fetch = mockFetch(200, { status: "reindex started" });
    vi.stubGlobal("fetch", fetch);

    const result = await triggerReindex();
    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(result.status).toBe("reindex started");
  });

  it("triggerReindex accepts optional params", async () => {
    const fetch = mockFetch(200, { status: "ok" });
    vi.stubGlobal("fetch", fetch);

    await triggerReindex({ file_ids: ["a", "b"], category: "reports" });

    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toContain("file_ids=a%2Cb");
    expect(url).toContain("category=reports");
  });

  // -- getReindexProgress ---------------------------------------------------

  it("getReindexProgress returns progress data", async () => {
    const progress: ReindexProgress = {
      total: 10,
      completed: 3,
      failed: 1,
      active: true,
    };
    const fetch = mockFetch(200, progress);
    vi.stubGlobal("fetch", fetch);

    const result = await getReindexProgress();
    expect(result).toEqual(progress);
    expect(fetch).toHaveBeenCalledWith("/api/files/reindex/progress", expect.anything());
  });

  // -- uploadFilesBatchWithProgress -----------------------------------------

  describe("uploadFilesBatchWithProgress", () => {
    it("resolves with file metadata on successful XHR upload", async () => {
      const meta = fakeFile;
      // Mock XMLHttpRequest
      const mockXHR = {
        open: vi.fn(),
        send: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { addEventListener: vi.fn() },
        addEventListener: vi.fn(),
        status: 200,
        statusText: "OK",
        responseText: JSON.stringify({ files: [meta] }),
      };

      // The constructor returns our mock; we also need to trigger the 'load' handler
      const OriginalXHR = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = vi.fn(() => mockXHR) as unknown as typeof XMLHttpRequest;

      // Capture the 'load' handler so we can fire it synchronously
      let loadHandler: (() => void) | undefined;
      mockXHR.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "load") loadHandler = handler;
        },
      );

      localStorage.setItem(TOKEN_KEY, "tok");

      const file = new File(["content"], "up.txt", { type: "text/plain" });
      const promise = uploadFilesBatchWithProgress([file], vi.fn());
      // Fire the load handler so the promise resolves
      loadHandler!();
      const result = await promise;

      expect(mockXHR.open).toHaveBeenCalledWith("POST", "/api/files/batch");
      expect(mockXHR.send).toHaveBeenCalled();
      expect(result).toEqual([meta]);

      globalThis.XMLHttpRequest = OriginalXHR;
    });

    it("rejects on non-OK XHR status", async () => {
      const mockXHR = {
        open: vi.fn(),
        send: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { addEventListener: vi.fn() },
        addEventListener: vi.fn(),
        status: 500,
        statusText: "Internal Server Error",
        responseText: "boom",
      };

      const OriginalXHR = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = vi.fn(() => mockXHR) as unknown as typeof XMLHttpRequest;

      let loadHandler: (() => void) | undefined;
      mockXHR.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "load") loadHandler = handler;
        },
      );

      const file = new File(["x"], "f.txt", { type: "text/plain" });
      const promise = uploadFilesBatchWithProgress([file], vi.fn());
      loadHandler!();

      await expect(promise).rejects.toThrow("500 Internal Server Error: boom");

      globalThis.XMLHttpRequest = OriginalXHR;
    });

    it("rejects on XHR network error", async () => {
      const mockXHR = {
        open: vi.fn(),
        send: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { addEventListener: vi.fn() },
        addEventListener: vi.fn(),
        status: 0,
      };

      const OriginalXHR = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = vi.fn(() => mockXHR) as unknown as typeof XMLHttpRequest;

      let errorHandler: (() => void) | undefined;
      mockXHR.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "error") errorHandler = handler;
        },
      );

      const file = new File(["x"], "f.txt", { type: "text/plain" });
      const promise = uploadFilesBatchWithProgress([file], vi.fn());
      errorHandler!();

      await expect(promise).rejects.toThrow("Network error during upload");

      globalThis.XMLHttpRequest = OriginalXHR;
    });

    it("rejects on XHR abort", async () => {
      const mockXHR = {
        open: vi.fn(),
        send: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { addEventListener: vi.fn() },
        addEventListener: vi.fn(),
        status: 0,
      };

      const OriginalXHR = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = vi.fn(() => mockXHR) as unknown as typeof XMLHttpRequest;

      let abortHandler: (() => void) | undefined;
      mockXHR.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "abort") abortHandler = handler;
        },
      );

      const file = new File(["x"], "f.txt", { type: "text/plain" });
      const promise = uploadFilesBatchWithProgress([file], vi.fn());
      abortHandler!();

      await expect(promise).rejects.toThrow("Upload aborted");

      globalThis.XMLHttpRequest = OriginalXHR;
    });

    it("fires progress callback with per-file progress", async () => {
      const mockXHR = {
        open: vi.fn(),
        send: vi.fn(),
        setRequestHeader: vi.fn(),
        upload: { addEventListener: vi.fn() },
        addEventListener: vi.fn(),
        status: 200,
        statusText: "OK",
        responseText: JSON.stringify({ files: [fakeFile, fakeFile] }),
      };

      const OriginalXHR = globalThis.XMLHttpRequest;
      globalThis.XMLHttpRequest = vi.fn(() => mockXHR) as unknown as typeof XMLHttpRequest;

      let progressHandler: ((e: { loaded: number; total: number; lengthComputable: boolean }) => void) | undefined;
      let loadHandler: (() => void) | undefined;

      mockXHR.upload.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "progress") progressHandler = handler;
        },
      );
      mockXHR.addEventListener.mockImplementation(
        (event: string, handler: () => void) => {
          if (event === "load") loadHandler = handler;
        },
      );

      const onProgress = vi.fn();
      const fileA = new File(["1234"], "a.txt"); // 4 bytes
      const fileB = new File(["567890"], "b.txt"); // 6 bytes, total = 10

      const promise = uploadFilesBatchWithProgress([fileA, fileB], onProgress);

      // Simulate progress: 5 bytes loaded out of 10
      progressHandler!({ loaded: 5, total: 10, lengthComputable: true });

      // Fire load to resolve
      loadHandler!();
      await promise;

      // onProgress should have been called twice (once per file)
      expect(onProgress).toHaveBeenCalledTimes(2);
      // fileA (index 0): bytes 0-4, so loaded = min(4, 5-0) = 4, progress = 4/4 = 1
      expect(onProgress).toHaveBeenCalledWith(0, 1);
      // fileB (index 1): bytes 4-10, so loaded = min(6, 5-4) = 1, progress = 1/6
      expect(onProgress).toHaveBeenCalledWith(1, 1 / 6);

      globalThis.XMLHttpRequest = OriginalXHR;
    });
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
