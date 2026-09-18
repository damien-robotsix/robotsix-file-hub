import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import SearchPage from "./SearchPage";
import * as api from "../api";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fakeResult(overrides: Partial<api.SearchResult> = {}): api.SearchResult {
  return {
    id: "abc-123",
    filename: "report.pdf",
    size: 2048,
    content_type: "application/pdf",
    checksum: "sha256:deadbeef",
    created_at: "2025-01-15T10:00:00Z",
    category: "reports",
    tags: "urgent,review",
    summary: "Quarterly report",
    source: null,
    relevance: 0.85,
    ...overrides,
  };
}

function fakeMetadata(overrides: Partial<api.FileMetadata> = {}): api.FileMetadata {
  return {
    id: "abc-123",
    filename: "report.pdf",
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

function renderSearchPage(initialQuery = "term") {
  return render(
    <MemoryRouter initialEntries={[`/search?q=${initialQuery}`]}>
      <Routes>
        <Route path="/search" element={<SearchPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("SearchPage", () => {
  let user: ReturnType<typeof userEvent.setup>;

  beforeEach(() => {
    vi.restoreAllMocks();
    user = userEvent.setup();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("fires search(query) with the URL query on mount", async () => {
    const searchSpy = vi.spyOn(api, "search").mockResolvedValue({
      results: [],
      total: 0,
      offset: 0,
      limit: 50,
      query: "term",
    });

    renderSearchPage("term");

    await waitFor(() => {
      expect(searchSpy).toHaveBeenCalledWith("term");
    });
  });

  it("renders the result list with filename, size, content type, relevance, and metadata", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      results: [fakeResult()],
      total: 1,
      offset: 0,
      limit: 50,
      query: "term",
    });

    renderSearchPage("term");

    await waitFor(() => {
      expect(screen.getByText("report.pdf")).toBeInTheDocument();
    });
    expect(screen.getByText("2.0 KB")).toBeInTheDocument();
    expect(screen.getByText("application/pdf")).toBeInTheDocument();
    expect(screen.getByText("85%")).toBeInTheDocument();
    expect(screen.getByText("reports")).toBeInTheDocument();
    expect(screen.getByText("Quarterly report")).toBeInTheDocument();
    expect(screen.getByText("urgent")).toBeInTheDocument();
    expect(screen.getByText("review")).toBeInTheDocument();
  });

  it("shows 'N results found' after a search completes", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      results: [fakeResult(), fakeResult({ id: "def-456", filename: "notes.txt" })],
      total: 2,
      offset: 0,
      limit: 50,
      query: "term",
    });

    renderSearchPage("term");

    await waitFor(() => {
      expect(screen.getByText("2 results found")).toBeInTheDocument();
    });
  });

  it("shows 'No files matched your query.' on an empty result set", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      results: [],
      total: 0,
      offset: 0,
      limit: 50,
      query: "term",
    });

    renderSearchPage("term");

    await waitFor(() => {
      expect(screen.getByText("No files matched your query.")).toBeInTheDocument();
    });
  });

  it("shows the error message when search rejects", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new Error("500 Internal Server Error: boom"));

    renderSearchPage("term");

    await waitFor(() => {
      expect(screen.getByText(/Error: 500 Internal Server Error: boom/)).toBeInTheDocument();
    });
  });

  it("submitting a new query updates the URL and re-fires search", async () => {
    const searchSpy = vi.spyOn(api, "search").mockResolvedValue({
      results: [],
      total: 0,
      offset: 0,
      limit: 50,
      query: "term",
    });

    renderSearchPage("term");

    await waitFor(() => {
      expect(searchSpy).toHaveBeenCalledWith("term");
    });

    const input = screen.getByPlaceholderText(
      "Search your files with natural language...",
    ) as HTMLInputElement;
    await user.clear(input);
    await user.type(input, "newquery");
    await user.click(screen.getByRole("button", { name: "Search" }));

    await waitFor(() => {
      expect(searchSpy).toHaveBeenCalledWith("newquery");
    });
  });

  it("renders the embedded FilePreview when a result title is clicked", async () => {
    vi.spyOn(api, "search").mockResolvedValue({
      results: [fakeResult({ content_type: "text/plain" })],
      total: 1,
      offset: 0,
      limit: 50,
      query: "term",
    });
    vi.spyOn(api, "getFileMetadata").mockResolvedValue(
      fakeMetadata({ content_type: "text/plain" }),
    );
    stubTextFetch("file text content"); // FilePreview's direct text fetch is the only fetch.

    renderSearchPage("term");

    await waitFor(() => {
      expect(screen.getByText("report.pdf")).toBeInTheDocument();
    });

    await user.click(screen.getByText("report.pdf"));

    await waitFor(() => {
      expect(screen.getByText("file text content")).toBeInTheDocument();
    });
    expect(api.getFileMetadata).toHaveBeenCalled();
  });
});