import { Routes, Route, Link } from "react-router-dom";
import HomePage from "./pages/HomePage.tsx";
import UploadPage from "./pages/UploadPage.tsx";
import SearchPage from "./pages/SearchPage.tsx";
import "./App.css";

export default function App() {
  return (
    <div className="app">
      <nav className="app-nav">
        <Link to="/">Home</Link>
        <Link to="/upload">Upload</Link>
        <Link to="/search">Search</Link>
      </nav>
      <main className="app-main">
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/upload" element={<UploadPage />} />
          <Route path="/search" element={<SearchPage />} />
        </Routes>
      </main>
    </div>
  );
}
