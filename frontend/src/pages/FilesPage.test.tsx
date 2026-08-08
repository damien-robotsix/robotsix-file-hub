import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider } from "../AuthContext";
import FilesPage from "./FilesPage";
import type { FileListResponse, FileMetadata, CategoriesResponse } from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const fakeFile: FileMetadata = {
  id: "abc-123",
  filename: "report.pdf",
  size: 2048,
  content_type: "application/pdf",
  checksum: "sha256:abc",
  storage_key: "sk-abc",
  created_at: "2025-01-15T10:00:00Z",
  category: "reports",
  tags: "urgent,review",
  summary: null,
  source: null,
};

const fakeFile2: FileMetadata = {
  id: "def-456",
  filename: "notes.txt",
  size: 512,
  content_type: "text/plain",
  checksum: "sha256:def",
  storage_key: "sk-def",
  created_at: "2025-01-16T12:00:00Z",
  category: null,
  tags: null,
  summary: null,
  source: null,
};

const listResponse: FileListResponse = {
  files: [fakeFile, fakeFile2],
  total: 2,
  offset: 0,
  limit: 20,
};

const categoriesResponse: CategoriesResponse = {
  categories: ["reports", "invoices"],
};

// ---------------------------------------------------------------------------
// Shared mock helpers
// ---------------------------------------------------------------------------

function stubFetchSequence(...bodies: unknown[]) {
  const calls = bodies.map((body) =>
    vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      text: () => Promise.resolve(JSON.stringify(body)),
      json: () => Promise.resolve(body),
    }),
  );
  // Chain them into a single mock that returns each call in sequence,
  // then the last one forever (safety for extra renders).
  let idx = 0;
  const mock = vi.fn(() => {
    const impl = idx < calls.length ? calls[idx] : calls[calls.length - 1];
    idx++;
    return impl();
  });
  return mock;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("FilesPage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  function renderFilesPage() {
    return render(
      <MemoryRouter initialEntries={["/files"]}>
        <AuthProvider>
          <FilesPage />
        </AuthProvider>
      </MemoryRouter>,
    );
  }

  it("renders the page heading", async () => {
    vi.stubGlobal("fetch", stubFetchSequence(categoriesResponse, listResponse));
    renderFilesPage();
    expect(screen.getByText("Files")).toBeTruthy();
    await waitFor(() => {
      expect(screen.getByText("report.pdf")).toBeTruthy();
    });
  });

  it("renders file rows when data loads", async () => {
    vi.stubGlobal("fetch", stubFetchSequence(categoriesResponse, listResponse));
    renderFilesPage();

    await waitFor(() => {
      expect(screen.getByText("report.pdf")).toBeTruthy();
      expect(screen.getByText("notes.txt")).toBeTruthy();
    });

    // Content types should appear (at least one cell each)
    expect(screen.getAllByText("application/pdf").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("text/plain").length).toBeGreaterThanOrEqual(1);

    // Category badge (also appears in the category dropdown)
    expect(screen.getAllByText("reports").length).toBeGreaterThanOrEqual(1);
  });

  it("shows 'No files found.' when the list is empty", async () => {
    vi.stubGlobal("fetch", stubFetchSequence(
      { categories: [] },
      { files: [], total: 0, offset: 0, limit: 20 },
    ));
    renderFilesPage();

    await waitFor(() => {
      expect(screen.getByText("No files found.")).toBeTruthy();
    });
  });

  it("shows an error message when the API call fails", async () => {
    const failMock = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: "OK",
        json: () => Promise.resolve({ categories: [] }),
        text: () => Promise.resolve(JSON.stringify({ categories: [] })),
      })
      .mockResolvedValueOnce({
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        json: () => Promise.reject(new Error("fail")),
        text: () => Promise.resolve("server error"),
      });
    vi.stubGlobal("fetch", failMock);
    renderFilesPage();

    await waitFor(() => {
      expect(screen.getByText(/500 Internal Server Error/)).toBeTruthy();
    });
  });

  it("renders the Upload button", async () => {
    vi.stubGlobal("fetch", stubFetchSequence(categoriesResponse, listResponse));
    renderFilesPage();

    await waitFor(() => {
      const buttons = screen.getAllByText("+ Upload");
      expect(buttons.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("renders the Reindex button", async () => {
    vi.stubGlobal("fetch", stubFetchSequence(categoriesResponse, listResponse));
    renderFilesPage();

    await waitFor(() => {
      const buttons = screen.getAllByText("Reindex");
      expect(buttons.length).toBeGreaterThanOrEqual(1);
    });
  });
});
