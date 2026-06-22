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

/**
 * @param {object} deps
 * @param {(req)=>object|null} deps.currentSession  resolve broker session from a request
 * @param {object} deps.store        store.js (github token storage)
 * @param {object} deps.secrets      secrets.js (encrypt/decrypt)
 * @param {object} deps.github       github.js (getUser/listRepos/getRepo)
 * @param {object} deps.agentProxy   agentProxy.js (forwardJson)
 * @returns {import('express').Router}
 */
export function createIdeRouter({ currentSession, store, secrets, github, agentProxy }) {
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

  return router;
}

export default createIdeRouter;
