/**
 * Server-side GitHub REST client for the Studio web IDE.
 *
 * The host is HARDCODED to https://api.github.com — it is never taken from user
 * input, which keeps these calls off the SSRF surface. The PAT travels only in
 * the Authorization header, never in a URL or log line.
 */
const API = 'https://api.github.com';
const HEADERS = (token) => ({
  accept: 'application/vnd.github+json',
  'user-agent': 'animica-studio',
  'x-github-api-version': '2022-11-28',
  // Only send Authorization when we actually have a token. A "Bearer " with an
  // empty token is rejected (401); public endpoints work unauthenticated.
  ...(token ? { authorization: `Bearer ${token}` } : {}),
});

async function ghFetch(token, path) {
  const res = await fetch(`${API}${path}`, { headers: HEADERS(token) });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  return { ok: res.ok, status: res.status, body };
}

/** GET /user — validates the token. Returns {login,name,avatar} or throws {status}. */
export async function getUser(token) {
  const { ok, status, body } = await ghFetch(token, '/user');
  if (!ok) throw Object.assign(new Error('github auth failed'), { status: status || 401 });
  return { login: body.login, name: body.name || body.login, avatar: body.avatar_url || '' };
}

/** GET /user/repos — most-recently-updated first. Returns array of repo summaries. */
export async function listRepos(token) {
  const { ok, status, body } = await ghFetch(token, '/user/repos?sort=updated&per_page=100');
  if (!ok) throw Object.assign(new Error('github repos failed'), { status: status || 502 });
  return (Array.isArray(body) ? body : []).map((r) => ({
    full_name: r.full_name,
    name: r.name,
    private: !!r.private,
    default_branch: r.default_branch,
    updated_at: r.updated_at,
  }));
}

/** GET /repos/{owner}/{name} — clone_url + default_branch + privacy. */
export async function getRepo(token, fullName) {
  // fullName is "owner/name"; encode each segment so it cannot inject path/query.
  const parts = String(fullName || '').split('/');
  if (parts.length !== 2 || !parts[0] || !parts[1]) {
    throw Object.assign(new Error('bad repo name'), { status: 400 });
  }
  const path = `/repos/${encodeURIComponent(parts[0])}/${encodeURIComponent(parts[1])}`;
  const { ok, status, body } = await ghFetch(token, path);
  if (!ok) throw Object.assign(new Error('github repo failed'), { status: status || 404 });
  return {
    full_name: body.full_name,
    clone_url: body.clone_url,
    default_branch: body.default_branch,
    private: !!body.private,
  };
}

// ---- OAuth App flow (optional; PAT still works without it) ----------------- #
const OAUTH_AUTHORIZE = 'https://github.com/login/oauth/authorize';
const OAUTH_TOKEN = 'https://github.com/login/oauth/access_token';

export function oauthConfigured() {
  return !!(process.env.GITHUB_CLIENT_ID && process.env.GITHUB_CLIENT_SECRET);
}

export function authorizeUrl(redirectUri, state, scope = 'repo') {
  const p = new URLSearchParams({
    client_id: process.env.GITHUB_CLIENT_ID || '',
    redirect_uri: redirectUri,
    scope,
    state,
    allow_signup: 'true',
  });
  return `${OAUTH_AUTHORIZE}?${p.toString()}`;
}

/** Exchange an OAuth `code` for a user access token. Host hardcoded to github.com. */
export async function exchangeOAuthCode(code, redirectUri) {
  const res = await fetch(OAUTH_TOKEN, {
    method: 'POST',
    headers: { accept: 'application/json', 'content-type': 'application/json', 'user-agent': 'animica-studio' },
    body: JSON.stringify({
      client_id: process.env.GITHUB_CLIENT_ID,
      client_secret: process.env.GITHUB_CLIENT_SECRET,
      code,
      redirect_uri: redirectUri,
    }),
  });
  let body = null;
  try { body = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok || !body || !body.access_token) {
    throw Object.assign(new Error('github oauth exchange failed'), { status: 502 });
  }
  return body.access_token;
}

export const config = { API };
