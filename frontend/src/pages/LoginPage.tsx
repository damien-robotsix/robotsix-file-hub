import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../AuthContext.tsx";

export default function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = token.trim();
    if (!trimmed) {
      setError("Please enter an API token.");
      return;
    }
    setError(null);
    login(trimmed);
    navigate("/", { replace: true });
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
