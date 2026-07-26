import { useState, type FormEvent } from "react";
import { Routes, Route, Link, useNavigate } from "react-router-dom";
import HomePage from "./pages/HomePage.tsx";
import UploadPage from "./pages/UploadPage.tsx";
import SearchPage from "./pages/SearchPage.tsx";
import FileDetailPage from "./pages/FileDetailPage.tsx";
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
      <nav className="app-nav">
        <Link to="/">Home</Link>
        <Link to="/upload">Upload</Link>
        <Link to="/search">Search</Link>
        <NavSearch />
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/search" element={<SearchPage />} />
          <Route path="/files/:fileId" element={<FileDetailPage />} />
        </Routes>
      </main>
    </div>
  );
}
