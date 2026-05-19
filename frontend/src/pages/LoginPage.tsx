// Login page. Shown when there's no stored JWT — replaces the whole app
// shell until login succeeds. Single-user, so no signup flow.

import { useState } from "react";

import { type AuthUser, login, storeToken } from "../api/auth";

interface Props {
  onAuthenticated: (user: AuthUser) => void;
}

export function LoginPage({ onAuthenticated }: Props) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const r = await login(email, password);
      storeToken(r.access_token);
      onAuthenticated(r.user);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={onSubmit}>
        <h1>Permian Type Curve</h1>
        <p className="muted">Sign in to continue.</p>

        <label className="login-label">Email</label>
        <input
          type="email"
          autoComplete="email"
          autoFocus
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />

        <label className="login-label">Password</label>
        <input
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="alert alert-error">{error}</div>}

        <button
          type="submit"
          className="btn-primary"
          disabled={submitting || !email || !password}
        >
          {submitting ? "signing in…" : "sign in"}
        </button>

        <p className="muted login-help">
          First-time setup? Run{" "}
          <code>docker compose exec backend python -m app.cli.create_user --email you@example.com</code>{" "}
          to create the account.
        </p>
      </form>
    </div>
  );
}
