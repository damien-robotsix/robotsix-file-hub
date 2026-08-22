import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

// ---------------------------------------------------------------------------
// Shared stub: provide a fetch that returns valid JSON for any URL so the
// pages (HomePage, FilesPage, etc.) don't crash on unhandled rejections.
// ---------------------------------------------------------------------------
function stubFetch() {
  const mock = vi.fn(async (url: string) => {
    // ConfigPanel fetches /config
    if (typeof url === "string" && url.includes("/config")) {
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: () =>
          Promise.resolve({
            config: { database_url: "sqlite:///test.db" },
            schema: { type: "object", properties: {} },
            version: 1,
          }),
        text: () => Promise.resolve(""),
      };
    }
    // Health check, file list, categories, etc.
    return {
      ok: true,
      status: 200,
      statusText: "OK",
      json: () => Promise.resolve({ status: "ok", files: [], total: 0, offset: 0, limit: 20, categories: [] }),
      text: () => Promise.resolve(JSON.stringify({ status: "ok", files: [], total: 0, offset: 0, limit: 20, categories: [] })),
    };
  });
  return mock;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", stubFetch());
  });

  function renderAt(path: string) {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <App />
      </MemoryRouter>,
    );
  }

  it("renders the AppShell with brand text", async () => {
    renderAt("/");
    expect(screen.getByText("File Hub")).toBeTruthy();
  });

  it("renders all primary nav links", () => {
    renderAt("/");
    for (const label of ["Home", "Files", "Upload", "Search"]) {
      expect(screen.getByText(label)).toBeTruthy();
    }
  });

  it("renders the Settings link", () => {
    renderAt("/");
    expect(screen.getByText("Settings")).toBeTruthy();
  });

  it("renders the search form in the right slot", () => {
    renderAt("/");
    const input = screen.getByPlaceholderText("Search files...");
    expect(input).toBeTruthy();
    expect(screen.getByLabelText("Search")).toBeTruthy();
  });

  it("renders the HomePage at /", () => {
    renderAt("/");
    expect(screen.getByText("Robotsix File Hub")).toBeTruthy();
  });

  it("renders the FilesPage at /files", () => {
    renderAt("/files");
    // "Files" appears in both the nav link and the page heading.
    const matches = screen.getAllByText("Files");
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });

  it("renders the UploadPage at /upload", () => {
    renderAt("/upload");
    expect(screen.getByText("Upload File")).toBeTruthy();
  });

  it("renders the SearchPage at /search", () => {
    renderAt("/search");
    // "Search" appears in both the nav link and the page heading.
    const matches = screen.getAllByText("Search");
    expect(matches.length).toBeGreaterThanOrEqual(2);
  });

  it("renders the Settings page at /settings", () => {
    renderAt("/settings");
    // ConfigPanel renders its title.
    expect(screen.getByText("File Hub Settings")).toBeTruthy();
  });
});