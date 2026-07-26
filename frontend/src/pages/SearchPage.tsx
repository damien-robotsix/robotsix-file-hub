import { useState, type FormEvent } from "react";
import { search, type SearchResult } from "../api.ts";

export default function SearchPage() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [searching, setSearching] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const res = await search(query);
      setResults(res.results);
    } catch (err: unknown) {
      setError(String(err));
    } finally {
      setSearching(false);
    }
  }

  return (
    <div>
      <h1>Search Files</h1>
      <form onSubmit={handleSubmit}>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search query..."
        />
        <button type="submit" disabled={!query.trim() || searching}>
          {searching ? "Searching..." : "Search"}
        </button>
      </form>
      {error && <p style={{ color: "red" }}>Error: {error}</p>}
      {results.length > 0 && (
        <ul>
          {results.map((r) => (
            <li key={r.file_id}>
              <strong>{r.filename}</strong> (score: {r.score.toFixed(3)})
              {r.snippet && <p>{r.snippet}</p>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
