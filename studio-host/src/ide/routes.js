/**
 * Web IDE broker routes (CONTRACT 2) — mounted at /api/ide by server.js.
 *
 * Everything here requires a valid broker session. GitHub connect/repos use the
 * user's stored PAT for server-side calls to https://api.github.com ONLY (the
 * host is hardcoded in ./github.js — no SSRF surface). FS/git endpoints proxy to
 * the user's per-container FastAPI agent sidecar via ./agentProxy.js.
 *
 * Anonymous sessions (tier==='anon') cannot connect a PAT and may only open
 * public repos.
 */
import express from 'express';
import { randomBytes } from 'node:crypto';

/**
 * @param {object} deps
 * @param {(req)=>object|null} deps.currentSession  resolve broker session from a request
 * @param {object} deps.store        store.js (github token storage)
 * @param {object} deps.secrets      secrets.js (encrypt/decrypt)
 * @param {object} deps.github       github.js (getUser/listRepos/getRepo)
 * @param {object} deps.agentProxy   agentProxy.js (forwardJson)
 * @param {object} [deps.proxy]      http-proxy instance (dev-server preview proxy)
 * @returns {import('express').Router}
 */
export function createIdeRouter({ currentSession, store, secrets, github, agentProxy, proxy }) {
  const router = express.Router();

  // ---- session gate ------------------------------------------------------- #
  // Attaches req.ideSession; 401 JSON if there is no valid session.
  router.use((req, res, next) => {
    const s = currentSession(req);
    if (!s) return res.status(401).json({ error: 'Not signed in.' });
    req.ideSession = s;
    next();
  });

  const isAnon = (s) => s.tier === 'anon';
  // Where this session's PAT lives: authed users key by email, anon by identity.
  const tokenKey = (s) => s.email || s.identity;

  function getToken(s) {
    const blob = store.getGithubToken(tokenKey(s));
    return blob ? secrets.decrypt(blob) : null;
  }

  // Per-user ENA inference key (each user supplies their own pool key).
  const ENA_BASE = (process.env.STUDIO_ENA_BASE || 'https://pool.animica.org/v1').replace(/\/+$/, '');
  // Free tier: a studio-funded shared key with a per-user DAILY quota (authed
  // users only, to resist anonymous abuse). Once spent, direct users to buy a key.
  const FREE_KEY = process.env.STUDIO_ENA_FREE_KEY || '';
  const FREE_LIMIT = parseInt(process.env.STUDIO_ENA_FREE_LIMIT || '20', 10);
  const BUY_URL = process.env.STUDIO_ENA_BUY_URL || 'https://pool.animica.org/keys';
  function freeStatus(s) {
    const enabled = !!FREE_KEY && !isAnon(s);
    const used = enabled ? store.getEnaFreeUsed(tokenKey(s)) : 0;
    return { enabled, limit: FREE_LIMIT, used, remaining: Math.max(0, FREE_LIMIT - used) };
  }
  function getEnaKey(s) {
    const blob = store.getEnaKey(tokenKey(s));
    return blob ? secrets.decrypt(blob) : null;
  }
  async function validateEnaKey(key) {
    // /v1/models is public, so validate against an auth-required endpoint. A tiny
    // embedding is the cheapest authenticated call (401/403 => the key is bad).
    try {
      const r = await fetch(`${ENA_BASE}/embeddings`, {
        method: 'POST',
        headers: { authorization: `Bearer ${key}`, 'content-type': 'application/json' },
        body: JSON.stringify({ model: 'anm-embed', input: 'ping' }),
      });
      return r.ok;
    } catch { return false; }
  }

  // Relay an error thrown by agentProxy/github (carries {status, body}).
  function relay(res, e, fallbackStatus = 502) {
    const status = e && e.status ? e.status : fallbackStatus;
    const body = e && e.body ? e.body : { error: e && e.message ? e.message : 'Request failed.' };
    res.status(status).json(body);
  }

  // ---- GitHub connect / status / disconnect ------------------------------- #
  router.post('/github/connect', async (req, res) => {
    const s = req.ideSession;
    if (isAnon(s)) return res.status(403).json({ error: 'Sign in to connect a GitHub account.' });
    const token = (req.body || {}).token;
    if (!token || typeof token !== 'string') return res.status(400).json({ error: 'A GitHub token is required.' });
    let user;
    try {
      user = await github.getUser(token);
    } catch (e) {
      return res.status(401).json({ error: 'GitHub rejected that token.' });
    }
    store.setGithubToken(tokenKey(s), secrets.encrypt(token));
    res.json({ login: user.login, name: user.name, avatar: user.avatar });
  });

  router.get('/github/status', async (req, res) => {
    const s = req.ideSession;
    const token = getToken(s);
    if (!token) return res.json({ connected: false });
    try {
      const user = await github.getUser(token);
      return res.json({ connected: true, login: user.login });
    } catch {
      // Token went stale/revoked — report disconnected (leave the blob; user can reconnect).
      return res.json({ connected: false });
    }
  });

  router.post('/github/disconnect', (req, res) => {
    const s = req.ideSession;
    store.clearGithubToken(tokenKey(s));
    res.json({ ok: true });
  });

  // ---- GitHub OAuth App flow (optional; PAT still works) ------------------- #
  const PUBLIC_BASE = (process.env.STUDIO_PUBLIC_BASE || 'https://studio.animica.org').replace(/\/+$/, '');
  const OAUTH_REDIRECT = `${PUBLIC_BASE}/api/ide/github/callback`;
  const oauthStates = new Map(); // state -> { identity, exp }
  const OAUTH_STATE_TTL = 10 * 60 * 1000;

  router.get('/github/oauth/available', (_req, res) => res.json({ available: github.oauthConfigured() }));

  router.get('/github/oauth/start', (req, res) => {
    const s = req.ideSession;
    if (isAnon(s)) return res.status(403).send('Sign in to connect GitHub.');
    if (!github.oauthConfigured()) return res.status(501).send('GitHub OAuth is not configured.');
    const state = randomBytes(16).toString('hex');
    oauthStates.set(state, { identity: tokenKey(s), exp: Date.now() + OAUTH_STATE_TTL });
    res.redirect(github.authorizeUrl(OAUTH_REDIRECT, state));
  });

  router.get('/github/callback', async (req, res) => {
    const code = req.query.code;
    const state = String(req.query.state || '');
    const entry = oauthStates.get(state);
    oauthStates.delete(state);
    const s = req.ideSession;
    if (!code || !entry || entry.exp < Date.now() || !s || tokenKey(s) !== entry.identity) {
      return res.redirect('/?github=error');
    }
    try {
      const token = await github.exchangeOAuthCode(String(code), OAUTH_REDIRECT);
      store.setGithubToken(tokenKey(s), secrets.encrypt(token));
      res.redirect('/?github=connected');
    } catch {
      res.redirect('/?github=error');
    }
  });

  // ---- repos -------------------------------------------------------------- #
  router.get('/github/repos', async (req, res) => {
    const s = req.ideSession;
    const token = getToken(s);
    if (!token) return res.status(409).json({ error: 'GitHub is not connected.' });
    try {
      const repos = await github.listRepos(token);
      res.json({ repos });
    } catch (e) {
      relay(res, e);
    }
  });

  // ---- open (clone) a repo into the user's container ----------------------- #
  router.post('/repo/open', async (req, res) => {
    const s = req.ideSession;
    const fullName = (req.body || {}).full_name;
    if (!fullName || typeof fullName !== 'string') {
      return res.status(400).json({ error: 'A repository (full_name) is required.' });
    }
    const token = getToken(s);

    // Resolve clone_url + default_branch + privacy from GitHub.
    let repo;
    try {
      if (token) {
        repo = await github.getRepo(token, fullName);
      } else {
        // Anonymous / not connected: only public repos are reachable. Try the
        // unauthenticated metadata fetch by passing an empty token — GitHub
        // serves public repo metadata without auth (rate-limited).
        repo = await github.getRepo('', fullName);
      }
    } catch (e) {
      return relay(res, e, 404);
    }

    if (repo.private && (isAnon(s) || !token)) {
      return res.status(403).json({ error: 'Sign in and connect GitHub to open a private repository.' });
    }

    // Proxy the clone to the sidecar. When the user is GitHub-connected we pass
    // the token so the sidecar can authenticate the clone (private repos) and
    // dodge anonymous rate limits; the sidecar uses it only at clone time and
    // never persists it in the container's git config (sidecar contract).
    const payload = { url: repo.clone_url, branch: repo.default_branch };
    if (token) payload.token = token;

    try {
      const out = await agentProxy.forwardJson(s, 'POST', '/git/clone', payload);
      res.json({ ok: true, repo: repo.full_name, branch: out.branch || repo.default_branch });
    } catch (e) {
      relay(res, e);
    }
  });

  router.get('/repo/status', async (req, res) => {
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'GET', '/git/status');
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  // ---- filesystem proxy --------------------------------------------------- #
  router.get('/fs/tree', async (req, res) => {
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'GET', '/fs/tree');
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  router.get('/fs/read', async (req, res) => {
    const path = req.query.path;
    if (typeof path !== 'string' || !path) return res.status(400).json({ error: 'path is required.' });
    try {
      const out = await agentProxy.forwardJson(
        req.ideSession, 'GET', `/fs/read?path=${encodeURIComponent(path)}`,
      );
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  router.put('/fs/write', async (req, res) => {
    const { path, content } = req.body || {};
    if (typeof path !== 'string' || !path) return res.status(400).json({ error: 'path is required.' });
    if (typeof content !== 'string') return res.status(400).json({ error: 'content (string) is required.' });
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'PUT', '/fs/write', { path, content });
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  // ---- ENA agent: streaming chat + diff approval -------------------------- #
  // The chat stream is proxied straight through to the sidecar with NO buffering
  // (streamProxy sets SSE headers + pipes upstream chunks to res as they arrive).
  // Errors from the sidecar (e.g. missing API key) come back as in-band SSE
  // `error` events, not HTTP error codes, so the client always sees them.
  // ---- ENA inference key (per-user; usage bills to the user's pool account) - #
  router.post('/ena/key', async (req, res) => {
    const key = (req.body || {}).key;
    if (!key || typeof key !== 'string') return res.status(400).json({ error: 'An ENA API key is required.' });
    if (!(await validateEnaKey(key))) return res.status(401).json({ error: 'That ENA key was rejected by the inference broker.' });
    store.setEnaKey(tokenKey(req.ideSession), secrets.encrypt(key));
    res.json({ connected: true });
  });
  router.get('/ena/key', (req, res) => {
    const s = req.ideSession;
    res.json({ connected: !!getEnaKey(s), free: freeStatus(s), buyUrl: BUY_URL });
  });
  router.post('/ena/key/disconnect', (req, res) => {
    store.clearEnaKey(tokenKey(req.ideSession));
    res.json({ ok: true });
  });

  router.post('/ena', async (req, res) => {
    const { message, history } = req.body || {};
    if (typeof message !== 'string' || !message) {
      return res.status(400).json({ error: 'message (string) is required.' });
    }
    const s = req.ideSession;
    const payload = { message };
    if (Array.isArray(history)) payload.history = history;

    // Key selection: the user's own key (no quota) → the free tier (authed, under
    // the daily limit) → otherwise a clean SSE error directing them to buy a key.
    let key = getEnaKey(s);
    let usedFree = false;
    if (!key) {
      const f = freeStatus(s);
      if (f.enabled && f.remaining > 0) { key = FREE_KEY; usedFree = true; }
    }
    if (!key) {
      const f = freeStatus(s);
      const msg = isAnon(s)
        ? 'Sign in to get free ENA messages, then add your own key to keep going.'
        : f.enabled
          ? `You've used your ${f.limit} free ENA messages for today. Get your own key to keep going.`
          : 'Connect your ENA key to chat.';
      res.writeHead(200, {
        'Content-Type': 'text/event-stream; charset=utf-8',
        'Cache-Control': 'no-cache, no-transform',
        'X-Accel-Buffering': 'no',
      });
      res.write(`event: error\ndata: ${JSON.stringify({ message: msg, needKey: true, buyUrl: BUY_URL })}\n\n`);
      res.write('event: done\ndata: {}\n\n');
      return res.end();
    }
    payload.ena_key = key;
    if (usedFree) { try { store.incrEnaFreeUsed(tokenKey(s)); } catch {} }
    try {
      await agentProxy.streamProxy(s, 'POST', '/ena/chat', payload, req, res);
    } catch (e) {
      // streamProxy only throws before headers are sent (e.g. no agent port);
      // once it has written SSE headers it handles errors in-band.
      if (!res.headersSent) relay(res, e);
      else { try { res.end(); } catch {} }
    }
  });

  // Approve / reject a pending diff proposal — unblocks the waiting tool in the
  // sidecar's agent loop (404 if the proposal id is unknown/expired).
  router.post('/ena/approve', async (req, res) => {
    const { id, accept } = req.body || {};
    if (typeof id !== 'string' || !id) return res.status(400).json({ error: 'id is required.' });
    if (typeof accept !== 'boolean') return res.status(400).json({ error: 'accept (boolean) is required.' });
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'POST', '/ena/approve', { id, accept });
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  // ---- source control: diff / commit / push ------------------------------ #
  router.get('/git/diff', async (req, res) => {
    const path = req.query.path;
    let sidePath = '/git/diff';
    if (typeof path === 'string' && path) sidePath += `?path=${encodeURIComponent(path)}`;
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'GET', sidePath);
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  router.post('/git/commit', async (req, res) => {
    const { message, paths } = req.body || {};
    if (typeof message !== 'string' || !message) {
      return res.status(400).json({ error: 'A commit message is required.' });
    }
    const payload = { message };
    if (Array.isArray(paths)) payload.paths = paths;
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'POST', '/git/commit', payload);
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  // Push to origin. The broker decrypts the session's GitHub PAT here and hands
  // it to the sidecar for THIS call only (the sidecar injects it into the https
  // remote URL at push time and never persists it). The token is never echoed
  // back to the client or logged.
  router.post('/git/push', async (req, res) => {
    const s = req.ideSession;
    if (isAnon(s)) return res.status(403).json({ error: 'Sign in and connect GitHub to push.' });
    const token = getToken(s);
    if (!token) return res.status(403).json({ error: 'GitHub is not connected.' });
    const { branch } = req.body || {};
    const payload = { token };
    if (typeof branch === 'string' && branch) payload.branch = branch;
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'POST', '/git/push', payload);
      res.json(out); // sidecar returns {ok, branch} — no token in the response
    } catch (e) { relay(res, e); }
  });

  // ---- run / preview (single in-container dev server) --------------------- #
  // start/stop/status are JSON RPCs to the sidecar; the actual dev-server HTTP
  // traffic is reverse-proxied below to the container's published DEV port.
  router.post('/preview/start', async (req, res) => {
    const cmd = (req.body || {}).cmd;
    const payload = {};
    if (typeof cmd === 'string' && cmd) payload.cmd = cmd;
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'POST', '/preview/start', payload);
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  router.post('/preview/stop', async (req, res) => {
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'POST', '/preview/stop', {});
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  router.get('/preview/status', async (req, res) => {
    try {
      const out = await agentProxy.forwardJson(req.ideSession, 'GET', '/preview/status');
      res.json(out);
    } catch (e) { relay(res, e); }
  });

  // Reverse-proxy the dev server itself. The browser loads the preview in an
  // iframe at /api/ide/preview/app/ ; we proxy everything under that prefix to
  // the container's published DEV port. WebSocket/HMR upgrades for this same
  // prefix are handled in server.js's upgrade handler.
  router.use('/preview/app', async (req, res) => {
    if (!proxy) return res.status(501).json({ error: 'Preview proxy not configured.' });
    let base;
    try {
      ({ base } = await agentProxy.getDevBase(req.ideSession));
    } catch (e) { return relay(res, e); }
    // The mount strips '/preview/app', so req.url is the path INTO the dev server
    // (e.g. '/' or '/assets/x.js'). An empty path means the iframe root.
    if (!req.url || req.url === '') req.url = '/';
    proxy.web(req, res, { target: base });
  });

  return router;
}

export default createIdeRouter;
