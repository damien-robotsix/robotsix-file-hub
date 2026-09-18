import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import FileDetailPage from "./FileDetailPage";
import * as api from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fakeMetadata(overrides: Partial<api.FileMetadata> = {}): api.FileMetadata {
  return {
    id: "abc-123",
    filename: "report.txt",
    size: 2048,
    content_type: "text/plain",
    checksum: "sha256:deadbeef",
    storage_key: "sk-1",
    created_at: "2025-01-15T10:00:00Z",
    category: "reports",
    tags: "urgent,review",
    summary: "Quarterly report",
    source: null,
    ...overrides,
  };
}

/** Resolve the direct fetch(downloadFileUrl(fileId)) text-content call. */
function stubTextFetch(text: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      text: () => Promise.resolve(text),
    }),
  );
}

function renderFileDetailPage(fileId = "abc-123") {
  return render(
    <MemoryRouter initialEntries={[`/files/${fileId}`]}>
      <Routes>
        <Route path="/" element={<p>Home Route Marker</p>} />
        <Route path="/files/:fileId" element={<FileDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("FileDetailPage", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows "Loading..." before the metadata resolves', () => {
    vi.spyOn(api, "getFileMetadata").mockReturnValue(
      new Promise<api.FileMetadata>(() => {}),
    );

    renderFileDetailPage();

    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("renders filename, type, size, checksum, and category/tags/summary after load", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(fakeMetadata());
    stubTextFetch("file text content");

    renderFileDetailPage();

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "report.txt" })).toBeInTheDocument();
    });
    expect(screen.getByText("text/plain")).toBeInTheDocument();
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("sha256:deadbeef")).toBeInTheDocument();
    expect(screen.getByText("reports")).toBeInTheDocument();
    expect(screen.getByText("urgent,review")).toBeInTheDocument();
    expect(screen.getByText("Quarterly report")).toBeInTheDocument();
  });

  it("shows an error and the back link when metadata loading fails", async () => {
    vi.spyOn(api, "getFileMetadata").mockRejectedValue(new Error("500 Internal Server Error: boom"));

    renderFileDetailPage();

    await waitFor(() => {
      expect(screen.getByText(/Error: 500 Internal Server Error: boom/)).toBeInTheDocument();
    });
    const backLink = screen.getByRole("link", { name: "← Back to Home" });
    expect(backLink).toHaveAttribute("href", "/");
  });

  it("renders the download link with href downloadFileUrl(id)", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(fakeMetadata());
    stubTextFetch("file text content");

    renderFileDetailPage();

    const download = await waitFor(() => {
      const link = screen.getByText("⬇ Download") as HTMLAnchorElement;
      expect(link).not.toBeNull();
      return link;
    });
    expect(download).toHaveAttribute("href", api.downloadFileUrl("abc-123"));
    expect(download).toHaveAttribute("download", "report.txt");
  });

  it("wires the delete button through handleDelete: confirm -> deleteFile -> navigate home", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(fakeMetadata());
    const deleteSpy = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    stubTextFetch("file text content");

    // happy-dom leaves window.confirm undefined, so stub it on both globalThis
    // and window (globalThis != window in happy-dom).
    const confirmSpy = vi.fn(() => true);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy as typeof window.confirm;

    renderFileDetailPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "🗑 Delete" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "🗑 Delete" }));

    await waitFor(() => {
      expect(confirmSpy).toHaveBeenCalledWith('Delete "report.txt"? This cannot be undone.');
    });
    expect(deleteSpy).toHaveBeenCalledWith("abc-123");

    // onAfterDelete navigates home (replace).
    await waitFor(() => {
      expect(screen.getByText("Home Route Marker")).toBeInTheDocument();
    });
  });

  it("does not call deleteFile when the user cancels the confirmation", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(fakeMetadata());
    const deleteSpy = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    stubTextFetch("file text content");

    const confirmSpy = vi.fn(() => false);
    vi.stubGlobal("confirm", confirmSpy);
    window.confirm = confirmSpy as typeof window.confirm;

    renderFileDetailPage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "🗑 Delete" })).toBeInTheDocument();
    });

    await user.click(screen.getByRole("button", { name: "🗑 Delete" }));

    expect(deleteSpy).not.toHaveBeenCalled();
    expect(screen.getByRole("heading", { name: "report.txt" })).toBeInTheDocument();
  });
});