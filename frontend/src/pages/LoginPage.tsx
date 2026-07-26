import { useState } from "react";
import { useNavigate, useLocation, Navigate } from "react-router-dom";
import { useAuth } from "../AuthContext.tsx";

export default function LoginPage() {
  const { login, isAuthenticated } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  // If already authenticated, redirect away from login
  if (isAuthenticated) {
    return <Navigate to="/" replace />;
  }

  const from = (location.state as { from?: { pathname: string } })?.from?.pathname || "/";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Please enter an API token.");
      return;
    }
    setError(null);
    login(trimmed);
    navigate(from, { replace: true });
  }

  return (
    <div className="login-page">
      <h1>Robotsix File Hub</h1>
      <form onSubmit={handleSubmit} className="login-form">
        <label htmlFor="token">API Token</label>
        <input
          id="token"
          type="password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="Enter your API token"
          autoFocus
        />
        {error && <p className="login-error">{error}</p>}
        <button type="submit">Log In</button>
      </form>
    </div>
  );
}
