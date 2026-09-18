import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import HomePage from "./HomePage";
import * as api from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fakeMetadata(overrides: Partial<api.FileMetadata> = {}): api.FileMetadata {
  return {
    id: "abc-123",
    filename: "report.pdf",
    size: 2048,
    content_type: "application/pdf",
    checksum: "sha256:deadbeef",
    storage_key: "sk-1",
    created_at: "2025-01-15T10:00:00Z",
    category: "reports",
    tags: "urgent,review",
    summary: null,
    source: null,
    ...overrides,
  };
}

function renderHomePage() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  );
}

describe("HomePage", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the Robotsix File Hub heading", () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    expect(screen.getByText("Robotsix File Hub")).toBeInTheDocument();
  });

  it("shows the backend status from healthCheck", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByText("ok")).toBeInTheDocument();
    });
  });

  it("lists recent files with filename and formatted size from listFiles", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [fakeMetadata(), fakeMetadata({ id: "def-456", filename: "notes.txt", size: 512 })],
      total: 2,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByText(/report\.pdf \(2\.0 KB\)/)).toBeInTheDocument();
    });
    expect(screen.getByText(/notes\.txt \(512 B\)/)).toBeInTheDocument();
  });

  it("shows 'No files uploaded yet.' on an empty list", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByText("No files uploaded yet.")).toBeInTheDocument();
    });
  });

  it("shows error text when healthCheck fails", async () => {
    vi.spyOn(api, "healthCheck").mockRejectedValue(new Error("500 Internal Server Error: boom"));
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByText(/Error: 500 Internal Server Error: boom/)).toBeInTheDocument();
    });
  });

  it("shows error text when listFiles fails", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockRejectedValue(new Error("list failed"));

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByText(/Error: list failed/)).toBeInTheDocument();
    });
  });

  it("opens the UploadDialog when '+ Upload' is clicked", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });

    renderHomePage();

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "+ Upload" })).toBeInTheDocument();
    });

    expect(screen.queryByText("Upload Files")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "+ Upload" }));
    expect(screen.getByText("Upload Files")).toBeInTheDocument();
  });

  it("triggers a list refresh when an upload completes", async () => {
    vi.spyOn(api, "healthCheck").mockResolvedValue({ status: "ok" });
    const listSpy = vi.spyOn(api, "listFiles").mockResolvedValue({
      files: [],
      total: 0,
      offset: 0,
      limit: 20,
    });
    vi.spyOn(api, "uploadFilesBatchWithProgress").mockResolvedValue([
      fakeMetadata({ filename: "hello.txt" }),
    ]);

    renderHomePage();

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledTimes(1);
    });

    await user.click(screen.getByRole("button", { name: "+ Upload" }));
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, new File(["hello"], "hello.txt", { type: "text/plain" }));
    await user.click(screen.getByRole("button", { name: "Upload (1)" }));

    await waitFor(() => {
      expect(listSpy).toHaveBeenCalledTimes(2);
    });
  });
});
