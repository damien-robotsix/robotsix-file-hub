import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadPage from "./UploadPage";
import * as api from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function renderUploadPage() {
  return render(<UploadPage />);
}

function fileInput(): HTMLInputElement {
  return document.querySelector('input[type="file"]') as HTMLInputElement;
}

describe("UploadPage", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  it("renders the upload form", () => {
    renderUploadPage();

    expect(screen.getByText("Upload File")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeInTheDocument();
    expect(fileInput()).toBeTruthy();
  });

  it("disables the submit button when no file is selected", () => {
    renderUploadPage();

    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("enables the submit button after a file is selected", async () => {
    renderUploadPage();

    await user.upload(fileInput(), new File(["hello"], "hello.txt", { type: "text/plain" }));

    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("renders 'Uploaded: <filename> (... KB)' after a successful upload", async () => {
    vi.spyOn(api, "uploadFile").mockResolvedValue({
      id: "abc-123",
      filename: "hello.txt",
      size: 2048,
      content_type: "text/plain",
      checksum: "sha256:deadbeef",
      storage_key: "sk-1",
      created_at: "2025-01-15T10:00:00Z",
      category: null,
      tags: null,
      summary: null,
      source: null,
    });

    renderUploadPage();

    await user.upload(fileInput(), new File(["hello"], "hello.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(screen.getByText(/Uploaded:/)).toBeInTheDocument();
    });
    expect(screen.getByText("hello.txt").closest("strong")).toHaveTextContent("hello.txt");
    expect(screen.getByText(/2\.0 KB/)).toBeInTheDocument();
  });

  it("renders the error message when the upload fails", async () => {
    vi.spyOn(api, "uploadFile").mockRejectedValue(new Error("500 Internal Server Error: boom"));

    renderUploadPage();

    await user.upload(fileInput(), new File(["hello"], "hello.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    await waitFor(() => {
      expect(screen.getByText(/Error: 500 Internal Server Error: boom/)).toBeInTheDocument();
    });
    expect(api.uploadFile).toHaveBeenCalledWith(expect.any(File));
  });

  it("shows 'Uploading...' and disables submit while the upload is pending", async () => {
    let resolveUpload: (value: api.UploadResponse) => void;
    const uploadPromise = new Promise<api.UploadResponse>((resolve) => {
      resolveUpload = resolve;
    });
    vi.spyOn(api, "uploadFile").mockReturnValue(uploadPromise);

    renderUploadPage();

    await user.upload(fileInput(), new File(["hello"], "hello.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Upload" }));

    expect(screen.getByRole("button", { name: "Uploading..." })).toBeDisabled();

    resolveUpload!({
      id: "abc-123",
      filename: "hello.txt",
      size: 42,
      content_type: "text/plain",
      checksum: "sha256:deadbeef",
      storage_key: "sk-1",
      created_at: "2025-01-15T10:00:00Z",
      category: null,
      tags: null,
      summary: null,
      source: null,
    });

    await waitFor(() => {
      expect(screen.getByText(/Uploaded:/)).toBeInTheDocument();
    });
  });
});