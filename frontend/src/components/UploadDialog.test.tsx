import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadDialog from "./UploadDialog";
import { uploadFilesBatchWithProgress, type FileMetadata } from "../api";

vi.mock("../api", async () => {
  const actual = await vi.importActual("../api");
  return { ...actual, uploadFilesBatchWithProgress: vi.fn() };
});

const mockUpload = uploadFilesBatchWithProgress as ReturnType<typeof vi.fn>;

function fm(overrides?: Partial<FileMetadata>): FileMetadata {
  return { id: "f1", filename: "a.txt", size: 100, content_type: "text/plain",
    checksum: "abc", storage_key: "sk", created_at: "2025-01-01T00:00:00Z",
    category: null, tags: null, summary: null, source: null, ...overrides };
}

describe("UploadDialog", () => {
  beforeEach(() => { vi.clearAllMocks(); });

  it("renders nothing when closed", () => {
    const { container } = render(
      <UploadDialog open={false} onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    expect(container.innerHTML).toBe("");
  });

  it("renders dialog when open", () => {
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    expect(screen.getByText("Upload Files")).toBeInTheDocument();
  });

  it("adds files via drop zone and shows them in list", async () => {
    const u = userEvent.setup();
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    expect(screen.getByText("test.txt")).toBeInTheDocument();
  });

  it("removes a file via remove button", async () => {
    const u = userEvent.setup();
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    await u.click(screen.getByTitle("Remove"));
    expect(screen.queryByText("test.txt")).not.toBeInTheDocument();
  });

  it("shows per-file progress bar during upload", async () => {
    const u = userEvent.setup();
    mockUpload.mockImplementation(
      (_files: File[], onP: (i: number, p: number) => void) =>
        new Promise(() => { onP(0, 0.5); }));
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    await u.click(screen.getByText("Upload (1)"));
    await waitFor(() => {
      const fill = document.querySelector(".progress-fill") as HTMLElement;
      expect(fill.style.width).toBe("50%");
    });
  });

  it("shows success status after upload", async () => {
    const u = userEvent.setup();
    mockUpload.mockResolvedValue([fm()]);
    const onC = vi.fn();
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={onC} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    await u.click(screen.getByText("Upload (1)"));
    await waitFor(() => { expect(screen.getByText(/Uploaded/)).toBeInTheDocument(); });
    expect(onC).toHaveBeenCalled();
  });

  it("shows error on batch failure", async () => {
    const u = userEvent.setup();
    mockUpload.mockRejectedValue(new Error("batch failed"));
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    await u.click(screen.getByText("Upload (1)"));
    await waitFor(() => { expect(screen.getByText("Error: batch failed")).toBeInTheDocument(); });
  });

  it("disables upload when no files selected", () => {
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    expect(screen.getByText("Upload")).toBeDisabled();
  });

  it('shows Close instead of Cancel when all done', async () => {
    const u = userEvent.setup();
    mockUpload.mockResolvedValue([fm()]);
    render(<UploadDialog open onClose={vi.fn()} onUploadComplete={vi.fn()} />);
    const f = new File(["hello"], "test.txt", { type: "text/plain" });
    const dz = screen.getByText(/Drag & drop files here/).closest(".upload-drop-zone")!;
    await u.upload(dz, f);
    await u.click(screen.getByText("Upload (1)"));
    await waitFor(() => { expect(screen.getByText("Close")).toBeInTheDocument(); });
  });
});
