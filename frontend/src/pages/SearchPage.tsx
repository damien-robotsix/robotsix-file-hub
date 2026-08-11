import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { search, downloadFileUrl, type SearchResult } from "../api.ts";
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
  const inputRef = useRef<HTMLInputElement>(null);
  const [results, setResults] = useState<SearchResult[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);

  // Sync the input with URL changes (e.g. when NavSearch navigates
  // here while we are already mounted) — use a DOM ref to avoid a
  // synchronous setState inside the effect.
  useEffect(() => {
    if (inputRef.current) {
      inputRef.current.value = initialQuery;
    }
  }, [initialQuery]);

  // Single source of truth: every time the URL query changes (including
  // the first mount with ?q=…), fire the search.  handleSubmit only
  // updates the URL — it never calls the API directly, avoiding the
  // stale-ref double-fire on initial form submit.
  useEffect(() => {
    if (!initialQuery) return;

    let cancelled = false;

    const runSearch = async () => {
      setSearching(true);
      setError(null);

      try {
        const res = await search(initialQuery);
        if (!cancelled) {
          setResults(res.results);
          setTotal(res.total);
          setSearched(true);
        }
      } catch (err: unknown) {
        if (!cancelled) setError(String(err));
      } finally {
        if (!cancelled) setSearching(false);
      }
    };

    runSearch();

    return () => {
      cancelled = true;
    };
  }, [initialQuery]);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const q = inputRef.current?.value.trim();
    if (!q) return;
    setSearchParams({ q });
  }

  return (
    <div className="search-page">
      <h1>Search Files</h1>
      <form onSubmit={handleSubmit} className="search-form">
        <input
          ref={inputRef}
          type="text"
          defaultValue={initialQuery}
          placeholder="Search your files with natural language..."
          className="search-input"
        />
        <button type="submit" disabled={searching} className="search-btn">
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
                  <img src={downloadFileUrl(r.id)} alt={r.filename} loading="lazy" />
                ) : (
                  <span className="result-file-icon">
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
