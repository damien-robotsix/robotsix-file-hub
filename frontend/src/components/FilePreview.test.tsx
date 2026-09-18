import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import FilePreview from "./FilePreview";
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

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FilePreview", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  // -- Loading state --------------------------------------------------------

  it('renders "Loading..." header before metadata resolves', () => {
    vi.spyOn(api, "getFileMetadata").mockReturnValue(
      new Promise<api.FileMetadata>(() => {}),
    );

    render(<FilePreview fileId="abc-123" />);

    expect(screen.getByText("Loading...")).toBeInTheDocument();
    expect(screen.queryByText("Loading text content...")).not.toBeInTheDocument();
  });

  it('renders "Loading text content..." before the text fetch resolves', async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "text/plain" }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockReturnValue(new Promise<Response>(() => {})),
    );

    render(<FilePreview fileId="abc-123" />);

    await waitFor(() => {
      expect(screen.getByText("Loading text content...")).toBeInTheDocument();
    });
  });

  // -- Error states ---------------------------------------------------------

  it("shows the error header and message when getFileMetadata rejects", async () => {
    vi.spyOn(api, "getFileMetadata").mockRejectedValue(new Error("boom"));

    render(<FilePreview fileId="abc-123" />);

    await waitFor(() => {
      expect(screen.getByText("Error loading file")).toBeInTheDocument();
    });
    expect(screen.getByText("Error: boom")).toBeInTheDocument();
  });

  it("shows the error header and message when the text fetch returns a non-ok status", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "text/plain" }),
    );
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        text: () => Promise.resolve("boom"),
      }),
    );

    render(<FilePreview fileId="abc-123" />);

    await waitFor(() => {
      expect(screen.getByText("Error loading file")).toBeInTheDocument();
    });
    expect(screen.getByText("Error: 500 Internal Server Error")).toBeInTheDocument();
  });

  // -- XSS regression guard -------------------------------------------------

  it("escapes file-controlled text content before injecting it into the DOM", async () => {
    const malicious =
      '<script>alert("xss")</script><img src=x onerror=alert(1)>';
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "text/html" }),
    );
    stubTextFetch(malicious);

    const { container } = render(<FilePreview fileId="abc-123" />);

    const pre = await waitFor(() => {
      const el = container.querySelector("pre.code-block");
      expect(el).not.toBeNull();
      return el as HTMLPreElement;
    });

    // No raw elements may be created in the DOM.
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();

    // The rendered markup is the escaped form, not the raw payload
    // (quotes re-serialize literally because they need no escaping in text).
    expect(pre.innerHTML).toBe(
      "&lt;script&gt;alert(\"xss\")&lt;/script&gt;" +
        "&lt;img src=x onerror=alert(1)&gt;",
    );

    // textContent still holds the original payload — escaped, not stripped.
    expect(pre.textContent).toBe(malicious);
  });

  // -- Image branch ---------------------------------------------------------

  it("renders an img with viewFileUrl src for image content types", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "image/png" }),
    );

    const { container } = render(<FilePreview fileId="abc-123" />);

    const img = await waitFor(() => {
      const el = container.querySelector("img");
      expect(el).not.toBeNull();
      return el as HTMLImageElement;
    });
    expect(img).toHaveAttribute("src", api.viewFileUrl("abc-123"));
    expect(img).toHaveAttribute("alt", "test.txt");
  });

  // -- PDF branch -----------------------------------------------------------

  it("renders an object with type application/pdf and a download fallback", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "application/pdf", filename: "report.pdf" }),
    );

    const { container } = render(<FilePreview fileId="abc-123" />);

    const objectEl = await waitFor(() => {
      const el = container.querySelector("object[type='application/pdf']");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(objectEl).toHaveAttribute("data", api.viewFileUrl("abc-123"));

    const fallback = screen.getByText("Download the PDF");
    expect(fallback).toHaveAttribute("href", api.downloadFileUrl("abc-123"));
  });

  // -- Unsupported branch ----------------------------------------------------

  it("renders the fallback icon and a not-available message for unsupported types", async () => {
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "application/octet-stream" }),
    );

    const { container } = render(<FilePreview fileId="abc-123" />);

    await waitFor(() => {
      expect(
        screen.getByText(/Preview not available for this file type/),
      ).toBeInTheDocument();
    });
    expect(container.querySelector(".fallback-icon")).not.toBeNull();
  });

  // -- Delete flow ----------------------------------------------------------

  it("calls deleteFile and onClose when the delete is confirmed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ filename: "notes.txt", content_type: "text/plain" }),
    );
    stubTextFetch("hello");
    const deleteSpy = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));

    render(<FilePreview fileId="abc-123" onClose={onClose} />);

    await user.click(await screen.findByRole("button", { name: /delete/i }));

    await waitFor(() => {
      expect(deleteSpy).toHaveBeenCalledWith("abc-123");
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("does not call deleteFile when the delete is cancelled", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ filename: "notes.txt", content_type: "text/plain" }),
    );
    stubTextFetch("hello");
    const deleteSpy = vi.spyOn(api, "deleteFile").mockResolvedValue(undefined);
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(false));

    render(<FilePreview fileId="abc-123" onClose={onClose} />);

    await user.click(await screen.findByRole("button", { name: /delete/i }));

    await waitFor(() => {
      expect(deleteSpy).not.toHaveBeenCalled();
    });
    expect(onClose).not.toHaveBeenCalled();
  });
});