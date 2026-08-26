import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { search, viewFileUrl, type SearchResult } from "../api.ts";
import { formatSize } from "../lib/format.ts";
import FilePreview from "../components/FilePreview.tsx";

function relevanceColor(score: number): string {
  if (score >= 0.7) return "#198754";
  if (score >= 0.4) return "#fd7e14";
  return "#6c757d";
}

function isImage(contentType: string | null): boolean {
  return !!contentType && contentType.startsWith("image/");
}

export default function SearchPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialQuery = searchParams.get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);

  // Keep the input field in sync with the URL (e.g. when NavSearch
  // navigates here while we are already mounted).
  useEffect(() => {
    // Sync local input state from the URL query parameter.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setQuery(initialQuery);
  }, [initialQuery]);

  // Single source of truth: every time the URL query changes (including
  // the first mount with ?q=…), fire the search.  handleSubmit only
  // updates the URL — it never calls the API directly, avoiding the
  // stale-ref double-fire on initial form submit.
  useEffect(() => {
    if (!initialQuery) {
      // Reset results when the URL has no query.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setResults([]);
      setTotal(0);
      setSearched(false);
      return;
    }

    let cancelled = false;
    Promise.resolve().then(() => {
      if (!cancelled) {
        setSearching(true);
        setError(null);
      }
    });

    search(initialQuery)
      .then((res) => {
        if (!cancelled) {
          setResults(res.results);
          setTotal(res.total);
          setSearched(true);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(String(err));
      })
      .finally(() => {
        if (!cancelled) setSearching(false);
      });

    return () => {
      cancelled = true;
    };
  }, [initialQuery]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSearchParams({ q });
  }

  return (
    <div className="search-page">
      <h1>Search Files</h1>
      <form onSubmit={handleSubmit} className="search-form">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search your files with natural language..."
          className="search-input"
        />
        <button type="submit" disabled={!query.trim() || searching} className="search-btn">
          {searching ? "Searching..." : "Search"}
        </button>
      </form>

      {selectedFileId && (
        <FilePreview fileId={selectedFileId} onClose={() => setSelectedFileId(null)} />
      )}

      {error && <p className="search-error">Error: {error}</p>}

      {searched && !searching && (
        <p className="search-count">
          {total} result{total !== 1 ? "s" : ""} found
        </p>
      )}

      {results.length > 0 && (
        <ul className="search-results">
          {results.map((r) => (
            <li key={r.id} className="search-result-item">
              <div className="result-thumbnail">
                {isImage(r.content_type) ? (
                  <img src={viewFileUrl(r.id)} alt={r.filename} loading="lazy" />
                ) : (
                  <span className="result-file-icon" aria-hidden="true">
                    {r.content_type === "application/pdf"
                      ? "📕"
                      : r.content_type?.startsWith("text/")
                        ? "📝"
                        : "📄"}
                  </span>
                )}
              </div>
              <div className="result-body">
                <Link
                  to={`/files/${r.id}`}
                  className="result-title"
                  onClick={(e) => {
                    e.preventDefault();
                    setSelectedFileId(r.id);
                  }}
                >
                  {r.filename}
                </Link>
                <div className="result-meta">
                  <span>{formatSize(r.size)}</span>
                  <span>{r.content_type}</span>
                  {r.category && <span className="result-category">{r.category}</span>}
                  <span>
                    Relevance:{" "}
                    <strong style={{ color: relevanceColor(r.relevance) }}>
                      {(r.relevance * 100).toFixed(0)}%
                    </strong>
                  </span>
                </div>
                {r.summary && <p className="result-summary">{r.summary}</p>}
                {r.tags && (
                  <p className="result-tags">
                    {r.tags.split(",").map((t) => (
                      <span key={t} className="tag">
                        {t.trim()}
                      </span>
                    ))}
                  </p>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}

      {searched && !searching && results.length === 0 && (
        <p className="search-empty">No files matched your query.</p>
      )}
    </div>
  );
}
