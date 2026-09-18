import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useFileDetail } from "./useFileDetail";
import * as api from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fakeMetadata(overrides: Partial<api.FileMetadata> = {}): api.FileMetadata {
  return {
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
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useFileDetail", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  // -- Fetch on mount -------------------------------------------------------

  it("fetches metadata once on mount and resolves metadata", async () => {
    const meta = fakeMetadata();
    const getFileMetadata = vi.spyOn(api, "getFileMetadata").mockResolvedValue(meta);

    const { result } = renderHook(() => useFileDetail("abc-123", () => {}));

    await waitFor(() => {
      expect(result.current.metadata).toEqual(meta);
    });
    expect(getFileMetadata).toHaveBeenCalledTimes(1);
    expect(getFileMetadata).toHaveBeenCalledWith("abc-123");
    expect(result.current.error).toBeNull();
  });

  // -- No fetch without id --------------------------------------------------

  it("does not fetch metadata when fileId is undefined", () => {
    const getFileMetadata = vi.spyOn(api, "getFileMetadata").mockResolvedValue(fakeMetadata());

    renderHook(() => useFileDetail(undefined, () => {}));

    expect(getFileMetadata).not.toHaveBeenCalled();
    expect(getFileMetadata).toHaveBeenCalledTimes(0);
  });

  // -- Fetch error ----------------------------------------------------------

  it("sets error when metadata fetch rejects", async () => {
    const boom = new Error("500 Internal Server Error: boom");
    vi.spyOn(api, "getFileMetadata").mockRejectedValue(boom);

    const { result } = renderHook(() => useFileDetail("abc-123", () => {}));

    await waitFor(() => {
      expect(result.current.error).toBe(String(boom));
    });
    expect(result.current.metadata).toBeNull();
  });

  // -- Delete confirmed -----------------------------------------------------

  it("confirms then calls deleteFile and onAfterDelete", async () => {
    const meta = fakeMetadata();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(meta);
    const confirmSpy = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy;
    const deleteFile = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    const onAfterDelete = vi.fn();

    const { result } = renderHook(() => useFileDetail("abc-123", onAfterDelete));
    await waitFor(() => {
      expect(result.current.metadata).toEqual(meta);
    });

    await act(async () => {
      await result.current.handleDelete();
    });

    expect(confirmSpy).toHaveBeenCalledWith('Delete "test.txt"? This cannot be undone.');
    expect(deleteFile).toHaveBeenCalledTimes(1);
    expect(deleteFile).toHaveBeenCalledWith("abc-123");
    expect(onAfterDelete).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  // -- Delete cancelled -----------------------------------------------------

  it("does not call deleteFile when confirm returns false", async () => {
    const meta = fakeMetadata();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(meta);
    const confirmSpy = vi.fn().mockReturnValue(false);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy;
    const deleteFile = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    const onAfterDelete = vi.fn();

    const { result } = renderHook(() => useFileDetail("abc-123", onAfterDelete));
    await waitFor(() => {
      expect(result.current.metadata).toEqual(meta);
    });

    await act(async () => {
      await result.current.handleDelete();
    });

    expect(deleteFile).not.toHaveBeenCalled();
    expect(onAfterDelete).not.toHaveBeenCalled();
    expect(result.current.deleting).toBe(false);
  });

  // -- Delete error ---------------------------------------------------------

  it("sets error and resets deleting when delete rejects", async () => {
    const meta = fakeMetadata();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(meta);
    const confirmSpy = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy;
    const boom = new Error("delete failed");
    vi.spyOn(api, "deleteFile").mockRejectedValue(boom);
    const onAfterDelete = vi.fn();

    const { result } = renderHook(() => useFileDetail("abc-123", onAfterDelete));
    await waitFor(() => {
      expect(result.current.metadata).toEqual(meta);
    });

    await act(async () => {
      await result.current.handleDelete();
    });

    expect(result.current.error).toBe(String(boom));
    expect(result.current.deleting).toBe(false);
    expect(onAfterDelete).not.toHaveBeenCalled();
  });

  // -- Pending state --------------------------------------------------------

  it("sets deleting to true while the delete promise is unresolved", async () => {
    const meta = fakeMetadata();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(meta);
    const confirmSpy = vi.fn().mockReturnValue(true);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy;
    let resolveDelete: () => void;
    const deletePromise = new Promise<void>((resolve) => {
      resolveDelete = resolve;
    });
    const deleteFile = vi.spyOn(api, "deleteFile").mockReturnValue(deletePromise);

    const { result } = renderHook(() => useFileDetail("abc-123", () => {}));
    await waitFor(() => {
      expect(result.current.metadata).toEqual(meta);
    });

    let handleDeletePromise: Promise<void>;
    act(() => {
      handleDeletePromise = result.current.handleDelete();
    });

    expect(result.current.deleting).toBe(true);

    await act(async () => {
      resolveDelete!();
      await handleDeletePromise!;
    });

    expect(deleteFile).toHaveBeenCalledTimes(1);
  });
});
