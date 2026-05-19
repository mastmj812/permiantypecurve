// Auth client + a centralized fetch helper that handles the JWT.
//
// Every component that talks to /api/* should use `apiFetch` so the
// Authorization header gets attached and 401s clear the token + bounce
// to the login page automatically.

const TOKEN_KEY = "permian_jwt";

export interface AuthUser {
  id: string;
  email: string;
  display_name: string | null;
  last_login_at: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  user: AuthUser;
}

export function getStoredToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* private mode / quota — single-user dev tool, accept the failure */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* same */
  }
}

// Called by `apiFetch` on any 401 to surface the logout to React. Set by
// the AuthProvider; default is a no-op so non-React callers still work.
let onUnauthorized: () => void = () => {};
export function setOnUnauthorized(fn: () => void): void {
  onUnauthorized = fn;
}

export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const token = getStoredToken();
  const headers = new Headers(init.headers);
  if (token && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const resp = await fetch(input, { ...init, headers });
  if (resp.status === 401) {
    clearToken();
    onUnauthorized();
  }
  return resp;
}

export async function login(email: string, password: string): Promise<LoginResponse> {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(r.status === 401 ? "invalid credentials" : `login failed: ${r.status} ${body}`);
  }
  return (await r.json()) as LoginResponse;
}

export async function logout(): Promise<void> {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch {
    /* server-side is a no-op anyway; we just want the token cleared */
  }
  clearToken();
}

export async function fetchMe(): Promise<AuthUser> {
  const r = await apiFetch("/api/auth/me");
  if (!r.ok) throw new Error(`me failed: ${r.status}`);
  return (await r.json()) as AuthUser;
}
