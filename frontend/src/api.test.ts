import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  listFiles,
  getFileMetadata,
  search,
  uploadFile,
  healthCheck,
  deleteFile,
  downloadFileUrl,
  type FileMetadata,
  type SearchResponse,
} from "./api";
import { TOKEN_KEY } from "./tokenStorage";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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
  id: "abc-123",
  filename: "test.txt",
  size: 42,
  content_type: "text/plain",
  checksum: "sha256:deadbeef",
  storage_key: "sk-1",
  created_at: "2025-01-15T10:00:00Z",
  category: null,
  tags: null,
  summary: null,
  source: null,
};

// ---------------------------------------------------------------------------
// Smoke tests
// ---------------------------------------------------------------------------

describe("API client", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  // -- Auth header injection ------------------------------------------------

  it("attaches Authorization header when token is present", async () => {
    const token = "my-jwt";
    localStorage.setItem(TOKEN_KEY, token);

    const fetch = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", fetch);

    await listFiles();

    const [url, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/files");
    expect((init.headers as Record<string, string>)["Authorization"]).toBe(`Bearer ${token}`);
  });

  it("omits Authorization header when no token is stored", async () => {
    const fetch = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", fetch);

    await listFiles();

    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Authorization"]).toBeUndefined();
  });

  it("still works when localStorage throws (e.g. in SSR/test env)", async () => {
    const original = localStorage.getItem;
    localStorage.getItem = () => {
      throw new Error("access denied");
    };
    const fetch = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", fetch);

    try {
      await listFiles();
      // shouldn't throw
    } finally {
      localStorage.getItem = original;
    }
  });

  // -- Error handling -------------------------------------------------------

  it("throws on non-OK HTTP status", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      text: () => Promise.resolve("boom"),
    });
    vi.stubGlobal("fetch", fetch);

    await expect(listFiles()).rejects.toThrow("500 Internal Server Error: boom");
  });

  it("returns undefined for 204 No Content", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      statusText: "No Content",
      text: () => Promise.resolve(""),
    });
    vi.stubGlobal("fetch", fetch);

    const result = await deleteFile("file-id");
    expect(result).toBeUndefined();
  });

  // -- Individual endpoints ------------------------------------------------

  it("listFiles builds query string from params", async () => {
    const fetch = mockFetch(200, { files: [fakeFile], total: 1, offset: 10, limit: 20 });
    vi.stubGlobal("fetch", fetch);

    await listFiles({ offset: 10, limit: 20, category: "reports", tag: "urgent" });

    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toContain("offset=10");
    expect(url).toContain("limit=20");
    expect(url).toContain("category=reports");
    expect(url).toContain("tag=urgent");
  });

  it("listFiles omits query string when no params given", async () => {
    const fetch = mockFetch(200, { files: [fakeFile], total: 1, offset: 0, limit: 20 });
    vi.stubGlobal("fetch", fetch);

    await listFiles();

    const [url] = fetch.mock.calls[0] as [string];
    expect(url).toBe("/api/files");
  });

  it("getFileMetadata returns file metadata", async () => {
    const fetch = mockFetch(200, fakeFile);
    vi.stubGlobal("fetch", fetch);

    const result = await getFileMetadata("abc-123");
    expect(result).toEqual(fakeFile);
    expect(fetch).toHaveBeenCalledWith("/api/files/abc-123/metadata", expect.anything());
  });

  it("search posts JSON to /files/search", async () => {
    const res: SearchResponse = {
      results: [],
      total: 0,
      offset: 0,
      limit: 50,
      query: "hello",
    };
    const fetch = mockFetch(200, res);
    vi.stubGlobal("fetch", fetch);

    const result = await search("hello");

    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ query: "hello", offset: 0, limit: 50 });
    expect(result).toEqual(res);
  });

  it("uploadFile sends multipart form data", async () => {
    const fetch = mockFetch(200, fakeFile);
    vi.stubGlobal("fetch", fetch);

    const file = new File(["content"], "test.txt", { type: "text/plain" });
    const result = await uploadFile(file);

    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(result).toEqual(fakeFile);
  });

  it("healthCheck returns status", async () => {
    const fetch = mockFetch(200, { status: "ok" });
    vi.stubGlobal("fetch", fetch);

    const result = await healthCheck();
    expect(result.status).toBe("ok");
  });

  it("downloadFileUrl returns the correct URL", () => {
    expect(downloadFileUrl("abc-123")).toBe("/api/files/abc-123");
  });

  it("deleteFile sends DELETE with X-Confirm-Delete header", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      statusText: "No Content",
      text: () => Promise.resolve(""),
    });
    vi.stubGlobal("fetch", fetch);

    await deleteFile("file-to-delete");

    const [, init] = fetch.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("DELETE");
    expect((init.headers as Record<string, string>)["X-Confirm-Delete"]).toBe("true");
  });
});
