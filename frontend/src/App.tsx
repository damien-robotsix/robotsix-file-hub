import { useState, type FormEvent } from "react";
import { Routes, Route, useNavigate } from "react-router-dom";
import { AppShell } from "@robotsix/ui";
import { ConfigPanel } from "@robotsix/ui";
import HomePage from "./pages/HomePage.tsx";
import UploadPage from "./pages/UploadPage.tsx";
import SearchPage from "./pages/SearchPage.tsx";
import FilesPage from "./pages/FilesPage.tsx";
import FileDetailPage from "./pages/FileDetailPage.tsx";
import ErrorBoundary from "./components/ErrorBoundary.tsx";
import "./App.css";

function NavSearch() {
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = q.trim();
    if (!trimmed) return;
    navigate(`/search?q=${encodeURIComponent(trimmed)}`);
  }

  return (
    <form className="nav-search" onSubmit={handleSubmit}>
      <input
        type="text"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search files..."
        className="nav-search-input"
      />
      <button type="submit" className="nav-search-btn" aria-label="Search">
        🔍
      </button>
    </form>
  );
}

export default function App() {
  return (
    <div className="app">
      <AppShell
        brand="File Hub"
        navItems={[
          { label: "Home", href: "/" },
          { label: "Files", href: "/files" },
          { label: "Upload", href: "/upload" },
          { label: "Search", href: "/search" },
        ]}
        settingsHref="/settings"
        rightSlot={<NavSearch />}
      />
      <main className="app-main">
        <Routes>
          <Route
            path="/"
            element={
              <ErrorBoundary>
                <HomePage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/files"
            element={
              <ErrorBoundary>
                <FilesPage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/files/:fileId"
            element={
              <ErrorBoundary>
                <FileDetailPage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/upload"
            element={
              <ErrorBoundary>
                <UploadPage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/search"
            element={
              <ErrorBoundary>
                <SearchPage />
              </ErrorBoundary>
            }
          />
          <Route
            path="/settings"
            element={
              <ErrorBoundary>
                <ConfigPanel title="File Hub Settings" />
              </ErrorBoundary>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
