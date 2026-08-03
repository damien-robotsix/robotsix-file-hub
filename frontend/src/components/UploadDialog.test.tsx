import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadDialog from "./UploadDialog";
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

describe("UploadDialog", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  // -- Render / visibility --------------------------------------------------

  it("renders nothing when open is false", () => {
    const { container } = render(
      <UploadDialog open={false} onClose={() => {}} onUploadComplete={() => {}} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("renders the dialog when open is true", () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);
    expect(screen.getByText("Upload Files")).toBeInTheDocument();
  });

  it("shows the drop zone with browse prompt", () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);
    expect(screen.getByText(/drag & drop files/i)).toBeInTheDocument();
    expect(screen.getByText(/browse/i)).toBeInTheDocument();
  });

  // -- Close / cancel -------------------------------------------------------

  it("calls onClose when the close button is clicked", async () => {
    const onClose = vi.fn();
    render(<UploadDialog open={true} onClose={onClose} onUploadComplete={() => {}} />);

    await user.click(screen.getByText("×"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onClose when Cancel button is clicked", async () => {
    const onClose = vi.fn();
    render(<UploadDialog open={true} onClose={onClose} onUploadComplete={() => {}} />);

    await user.click(screen.getByText("Cancel"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  // -- File selection via input ---------------------------------------------

  it("adds files when a file input change occurs", async () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    expect(screen.getByText("hello.txt")).toBeInTheDocument();
  });

  it("adds multiple files at once", async () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const fileA = new File(["a"], "a.txt", { type: "text/plain" });
    const fileB = new File(["b"], "b.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, [fileA, fileB]);

    expect(screen.getByText("a.txt")).toBeInTheDocument();
    expect(screen.getByText("b.txt")).toBeInTheDocument();
  });

  // -- Remove file ----------------------------------------------------------

  it("removes a file when its remove button is clicked", async () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    expect(screen.getByText("hello.txt")).toBeInTheDocument();

    await user.click(screen.getByTitle("Remove"));

    expect(screen.queryByText("hello.txt")).not.toBeInTheDocument();
  });

  // -- Upload button state --------------------------------------------------

  it("disables the upload button when no files are selected", () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("enables the upload button when files are selected", async () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    expect(screen.getByRole("button", { name: "Upload (1)" })).toBeEnabled();
  });

  // -- Upload success -------------------------------------------------------

  it("shows success status after a successful upload", async () => {
    const meta = fakeMetadata({ filename: "hello.txt", size: 1024 });
    vi.spyOn(api, "uploadFilesBatchWithProgress").mockResolvedValue([meta]);

    const onUploadComplete = vi.fn();
    render(
      <UploadDialog open={true} onClose={() => {}} onUploadComplete={onUploadComplete} />,
    );

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    await user.click(screen.getByRole("button", { name: "Upload (1)" }));

    await waitFor(() => {
      expect(screen.getByText(/uploaded/i)).toBeInTheDocument();
    });
    expect(onUploadComplete).toHaveBeenCalledTimes(1);
  });

  // -- Upload loading state -------------------------------------------------

  it("shows Uploading... text on the button during upload", async () => {
    let resolveUpload: (value: api.FileMetadata[]) => void;
    const uploadPromise = new Promise<api.FileMetadata[]>((resolve) => {
      resolveUpload = resolve;
    });
    vi.spyOn(api, "uploadFilesBatchWithProgress").mockReturnValue(uploadPromise);

    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    await user.click(screen.getByRole("button", { name: "Upload (1)" }));

    // Button should show "Uploading..." while the promise is pending
    expect(screen.getByRole("button", { name: "Uploading..." })).toBeInTheDocument();
    // × close button should be disabled during upload
    expect(screen.getByText("×")).toBeDisabled();

    // Resolve the upload
    resolveUpload!([fakeMetadata({ filename: "hello.txt" })]);
    await waitFor(() => {
      expect(screen.getByText(/uploaded/i)).toBeInTheDocument();
    });
  });

  // -- Upload error ---------------------------------------------------------

  it("shows error status after a failed upload", async () => {
    vi.spyOn(api, "uploadFilesBatchWithProgress").mockRejectedValue(
      new Error("500 Internal Server Error: boom"),
    );

    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["hello"], "hello.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    await user.click(screen.getByRole("button", { name: "Upload (1)" }));

    await waitFor(() => {
      expect(screen.getByText(/500 Internal Server Error: boom/)).toBeInTheDocument();
    });
  });

  // -- Drag-and-drop --------------------------------------------------------

  it("adds files via drag-and-drop onto the drop zone", () => {
    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const dropZone = document.querySelector(".upload-drop-zone")!;
    const file = new File(["hello"], "drop.txt", { type: "text/plain" });

    // Simulate a drop event carrying files via DataTransfer
    const dt = new DataTransfer();
    dt.items.add(file);
    fireEvent.drop(dropZone, { dataTransfer: dt });

    expect(screen.getByText("drop.txt")).toBeInTheDocument();
  });

  // -- All-done state -------------------------------------------------------

  it("shows Close button text after all uploads complete", async () => {
    const meta = fakeMetadata({ filename: "ok.txt" });
    vi.spyOn(api, "uploadFilesBatchWithProgress").mockResolvedValue([meta]);

    render(<UploadDialog open={true} onClose={() => {}} onUploadComplete={() => {}} />);

    const file = new File(["x"], "ok.txt", { type: "text/plain" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);
    await user.click(screen.getByRole("button", { name: "Upload (1)" }));

    await waitFor(() => {
      expect(screen.getByText("Close")).toBeInTheDocument();
    });
  });
});
