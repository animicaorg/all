import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";
import { login } from "../lib/api";

export function LoginPage() {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const auth = useAuth();
  const navigate = useNavigate();

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const response = await login(username, password);
      auth.setAuth({
        token: response.access_token,
        role: response.role,
        username
      });
      navigate("/", { replace: true });
    } catch (err: any) {
      setError(err?.response?.data?.detail || err?.message || "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-shell">
      <div className="admin-login-card">
        <h1 style={{ marginTop: 0 }}>BANM Bridge Admin</h1>
        <form onSubmit={onSubmit} className="admin-form">
          <label>
            Username
            <input value={username} onChange={(e) => setUsername(e.target.value)} />
          </label>
          <label>
            Password
            <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </label>
          <button disabled={busy}>{busy ? "Signing in..." : "Sign In"}</button>
        </form>
        {error && <div className="admin-error">{error}</div>}
      </div>
    </div>
  );
}

