import { useState, type FormEvent } from "react";
import { Navigate, Routes, Route, Link, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext.tsx";
import HomePage from "./pages/HomePage.tsx";
import UploadPage from "./pages/UploadPage.tsx";
import SearchPage from "./pages/SearchPage.tsx";
import LoginPage from "./pages/LoginPage.tsx";
import FilesPage from "./pages/FilesPage.tsx";
import FileDetailPage from "./pages/FileDetailPage.tsx";
import "./App.css";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}

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

function AppNav() {
  const { isAuthenticated, logout } = useAuth();

  if (!isAuthenticated) return null;

  return (
    <nav className="app-nav">
      <Link to="/">Home</Link>
      <Link to="/files">Files</Link>
      <Link to="/upload">Upload</Link>
      <Link to="/search">Search</Link>
      <NavSearch />
      <button onClick={logout} className="nav-logout">
        Log out
      </button>
    </nav>
  );
}

export default function App() {
  return (
    <div className="app">
      <AppNav />
      <main className="app-main">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAuth>
                <HomePage />
              </RequireAuth>
            }
          />
          <Route
            path="/files"
            element={
              <RequireAuth>
                <FilesPage />
              </RequireAuth>
            }
          />
          <Route
            path="/files/:fileId"
            element={
              <RequireAuth>
                <FileDetailPage />
              </RequireAuth>
            }
          />
          <Route
            path="/upload"
            element={
              <RequireAuth>
                <UploadPage />
              </RequireAuth>
            }
          />
          <Route
            path="/search"
            element={
              <RequireAuth>
                <SearchPage />
              </RequireAuth>
            }
          />
        </Routes>
      </main>
    </div>
  );
}
