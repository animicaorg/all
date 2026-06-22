/**
 * Animica Studio host — session broker + auth gateway.
 *
 * Serves a login page at /. Only AFTER a valid session does it reverse-proxy the
 * noVNC stream (HTTP + websocket) to that user's OWN isolated container. So the
 * public endpoint is authenticated — never an open shell.
 *
 * Auth methods: email+password, anonymous (hardened), email magic-link (when
 * SMTP is configured). Wallet + subscriptions land in later phases.
 */
import http from 'node:http';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import express from 'express';
import httpProxy from 'http-proxy';
import * as cookie from 'cookie';
import bcrypt from 'bcryptjs';
import { nanoid } from 'nanoid';

import * as sessions from './sessions.js';
import { ensureSession, touch, destroy, startReaper, stats, config } from './sessions.js';
import * as store from './store.js';
import { mailerReady, sendMagicLink } from './mailer.js';
import * as billing from './billing.js';
import * as totp from './totp.js';
import { createIdeRouter } from './ide/routes.js';
import { createWalletConnectRouter, createWalletSignRouter } from './ide/walletConnect.js';
import * as ideSecrets from './ide/secrets.js';
import * as ideGithub from './ide/github.js';
import * as ideAgentProxy from './ide/agentProxy.js';
import { attachTerminal, terminalAvailable } from './ide/terminal.js';

// --- tiny .env loader (no dep) --------------------------------------------- #
const __dir = dirname(fileURLToPath(import.meta.url));
(function loadEnv() {
  const f = join(__dir, '..', '.env');
  if (!existsSync(f)) return;
  for (const line of readFileSync(f, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
    if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
})();

const PORT = parseInt(process.env.PORT || '8099', 10);
const COOKIE = 'anm_sid';
const PUBLIC_DIR = join(__dir, '..', 'public');
const INDEX_HTML = join(PUBLIC_DIR, 'index.html');
// New mobile-first web IDE SPA (studio-ide). Served at / as the default app;
// the legacy email/password login page moves to /login, the noVNC desktop to /desktop.
const SPA_DIR = process.env.STUDIO_IDE_DIST || join(__dir, '..', '..', 'studio-ide', 'dist');
const SPA_INDEX = join(SPA_DIR, 'index.html');
const SPA_READY = existsSync(SPA_INDEX);
const ANON_ENABLED = process.env.STUDIO_ALLOW_ANON !== '0';
// noVNC viewer, auto-connecting through the gated /app proxy.
// Mobile gets resize=scale (fit the whole desktop to the phone screen + touch
// cursor dot) since the in-container Xvfb is a fixed size and resize=remote is a
// no-op without x11vnc -xrandr; desktop keeps resize=remote. One source of truth.
function viewerUrl(req) {
  const ua = (req && req.headers && req.headers['user-agent']) || '';
  const isMobile = /Android|iPhone|iPad|iPod|IEMobile|Opera Mini|Mobile|Silk|Kindle/i.test(ua);
  const resize = isMobile ? 'scale' : 'remote';
  let u = `/app/vnc.html?autoconnect=true&resize=${resize}&reconnect=true&path=app/websockify`;
  if (isMobile) u += '&show_dot=true';
  return u;
}

const proxy = httpProxy.createProxyServer({ ws: true, xfwd: true });
proxy.on('error', (_e, _req, res) => { try { res.writeHead?.(502); res.end?.('studio backend unavailable'); } catch {} });

const app = express();
app.disable('x-powered-by');
// JSON body for everything EXCEPT the NOWPayments IPN, which needs the raw bytes
// for HMAC signature verification.
app.use((req, res, next) => {
  if (req.path === '/api/billing/nowpayments/ipn') return next();
  // The ENA chat route carries the prompt + history + (potentially) large file
  // context, so it gets a roomier JSON limit than the default 256kb.
  if (req.path === '/api/ide/ena') return express.json({ limit: '4mb' })(req, res, next);
  express.json({ limit: '256kb' })(req, res, next);
});

// --- helpers --------------------------------------------------------------- #
const validEmail = (e) => typeof e === 'string' && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e);
const clientIp = (req) => (req.headers['x-forwarded-for'] || '').split(',')[0].trim() || req.socket.remoteAddress || 'unknown';
const publicBase = (req) => `${req.headers['x-forwarded-proto'] || 'https'}://${req.headers['host']}`;

function getSid(req) { return cookie.parse(req.headers.cookie || '')[COOKIE]; }
function currentSession(req) { const sid = getSid(req); return sid ? store.getSession(sid) : null; }
function setCookie(res, sid, ttlMs) {
  res.setHeader('Set-Cookie', cookie.serialize(COOKIE, sid, {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: Math.floor(ttlMs / 1000),
  }));
}
function clearCookie(res) {
  res.setHeader('Set-Cookie', cookie.serialize(COOKIE, '', { httpOnly: true, path: '/', maxAge: 0 }));
}

function startUserSession(res, user) {
  const ttl = 7 * 24 * 3600 * 1000;
  const sid = store.createSession({ identity: user.id, tier: 'standard', email: user.email, plan: user.plan, ttlMs: ttl });
  setCookie(res, sid, ttl);
  ensureSession(user.id, { tier: 'standard', persistent: user.plan === 'pro' }).catch(() => {}); // pre-warm
}

const rate = new Map(); // ip -> { n, reset }
function rateOk(ip, max, windowMs) {
  const now = Date.now();
  const r = rate.get(ip);
  if (!r || now > r.reset) { rate.set(ip, { n: 1, reset: now + windowMs }); return true; }
  if (r.n >= max) return false;
  r.n++; return true;
}

// --- auth routes ----------------------------------------------------------- #
app.post('/api/signup', async (req, res) => {
  const { email, password } = req.body || {};
  if (!validEmail(email) || !password || password.length < 8) {
    return res.status(400).json({ error: 'A valid email and an 8+ character password are required.' });
  }
  let user;
  try { user = store.createUser({ email, passHash: await bcrypt.hash(password, 10) }); }
  catch { return res.status(409).json({ error: 'That account already exists — try logging in.' }); }
  startUserSession(res, user);
  res.json({ ok: true, redirect: '/' });
});

app.post('/api/login', async (req, res) => {
  const { email, password, code } = req.body || {};
  const user = store.findUserByEmail(email || '');
  if (!user || !user.passHash || !(await bcrypt.compare(password || '', user.passHash))) {
    return res.status(401).json({ error: 'Invalid username or password.' });
  }
  if (user.totpEnabled) {
    if (!code) return res.status(401).json({ error: 'Enter the code from your authenticator app.', twofa: true });
    if (!totp.verify(user.totpSecret, code)) return res.status(401).json({ error: 'That authenticator code is wrong or expired.', twofa: true });
  }
  startUserSession(res, user);
  res.json({ ok: true, redirect: '/' });
});

// --- optional authenticator (TOTP 2FA) ------------------------------------- #
function requireUser(req, res) {
  const s = currentSession(req);
  if (!s || !s.email) { res.status(401).json({ error: 'Not signed in.' }); return null; }
  return s;
}
app.post('/api/2fa/setup', (req, res) => {
  const s = requireUser(req, res); if (!s) return;
  const secret = totp.generateSecret();
  store.setTotpPending(s.email, secret);
  res.json({ ok: true, secret, otpauth: totp.otpauthURL(s.email, secret) });
});
app.post('/api/2fa/enable', (req, res) => {
  const s = requireUser(req, res); if (!s) return;
  const u = store.findUserByEmail(s.email);
  if (!u || !u.totpPending) return res.status(400).json({ error: 'Start setup first.' });
  if (!totp.verify(u.totpPending, (req.body || {}).code)) return res.status(400).json({ error: 'Code did not match — try again.' });
  store.enableTotp(s.email);
  res.json({ ok: true });
});
app.post('/api/2fa/disable', async (req, res) => {
  const s = requireUser(req, res); if (!s) return;
  const u = store.findUserByEmail(s.email);
  if (!u || !u.passHash || !(await bcrypt.compare((req.body || {}).password || '', u.passHash))) {
    return res.status(401).json({ error: 'Password required to turn off 2FA.' });
  }
  store.disableTotp(s.email);
  res.json({ ok: true });
});

app.post('/api/anon', async (req, res) => {
  if (!ANON_ENABLED) return res.status(403).json({ error: 'Anonymous mode is disabled.' });
  if (!rateOk(clientIp(req), 5, 3600_000)) {
    return res.status(429).json({ error: 'Too many anonymous sessions from your network. Try later or sign up.' });
  }
  const id = 'anon-' + nanoid(10);
  const ttl = config.ANON_TTL_MS;
  const sid = store.createSession({ identity: id, tier: 'anon', plan: 'anon', ttlMs: ttl });
  try {
    await ensureSession(id, { tier: 'anon', persistent: false });
  } catch (e) {
    store.deleteSession(sid);
    return res.status(503).json({ error: e.code === 'AT_CAPACITY' ? 'Studio is at capacity — try again shortly.' : 'Could not start a session.' });
  }
  setCookie(res, sid, ttl);
  res.json({ ok: true, redirect: '/' });
});

app.post('/api/logout', async (req, res) => {
  const sid = getSid(req);
  if (sid) {
    const s = store.getSession(sid);
    if (s && s.tier === 'anon') destroy(s.identity).catch(() => {});
    store.deleteSession(sid);
  }
  clearCookie(res);
  res.json({ ok: true });
});

app.get('/api/me', (req, res) => {
  const s = currentSession(req);
  const u = s?.email ? store.findUserByEmail(s.email) : null;
  res.json({
    authed: !!s, email: s?.email || null, tier: s?.tier || null,
    plan: u?.plan || s?.plan || null, planUntil: u?.planUntil || null, twofa: !!u?.totpEnabled,
    methods: { password: true, anon: ANON_ENABLED, magic: mailerReady(), wallet: false },
    billing: { nowpayments: billing.nowpaymentsReady(), priceUsd: billing.PRICE_USD() },
  });
});

// --- magic link (active only when SMTP configured) ------------------------- #
app.post('/api/magic/request', async (req, res) => {
  if (!mailerReady()) return res.status(501).json({ error: 'Email sign-in is not configured yet.' });
  const { email } = req.body || {};
  if (!validEmail(email)) return res.status(400).json({ error: 'A valid email is required.' });
  if (!rateOk('magic:' + clientIp(req), 5, 3600_000)) return res.status(429).json({ error: 'Too many requests — try later.' });
  const token = store.createMagicToken(email);
  try { await sendMagicLink(email, `${publicBase(req)}/api/magic/verify?token=${token}`); }
  catch { return res.status(500).json({ error: 'Could not send the email. Try another method.' }); }
  res.json({ ok: true });
});

app.get('/api/magic/verify', (req, res) => {
  const email = store.consumeMagicToken(String(req.query.token || ''));
  if (!email) return res.status(400).send('This sign-in link is invalid or has expired.');
  startUserSession(res, store.upsertUserByEmail(email));
  res.redirect('/');
});

// --- wallet (phase 2) ------------------------------------------------------ #
app.all('/api/wallet/*', (_req, res) => res.status(501).json({ error: 'Wallet sign-in arrives in phase 2.' }));

// --- subscriptions (NOWPayments) ------------------------------------------- #
app.post('/api/billing/nowpayments/create', async (req, res) => {
  const s = currentSession(req);
  if (!s || !s.email) return res.status(401).json({ error: 'Log in with email to subscribe.' });
  if (!billing.nowpaymentsReady()) return res.status(501).json({ error: 'Payments are not configured yet.' });
  try {
    const inv = await billing.createInvoice({ email: s.email, base: publicBase(req) });
    res.json({ ok: true, url: inv.url });
  } catch {
    res.status(502).json({ error: 'Could not start checkout. Please try again.' });
  }
});

// NOWPayments posts a signed IPN here (raw body parsed above).
app.post('/api/billing/nowpayments/ipn', express.raw({ type: '*/*', limit: '256kb' }), (req, res) => {
  const raw = Buffer.isBuffer(req.body) ? req.body.toString('utf8') : '';
  if (!billing.verifyIpn(raw, req.headers['x-nowpayments-sig'])) return res.status(401).end();
  let data; try { data = JSON.parse(raw); } catch { return res.status(400).end(); }
  if (billing.PAID_STATUSES.has(data.payment_status)) {
    const email = billing.emailFromOrder(data.order_id);
    if (email) store.setUserPlan(email, 'pro', Date.now() + billing.PLAN_DAYS() * 86400000);
  }
  res.json({ ok: true });
});

// --- ops ------------------------------------------------------------------- #
app.get('/healthz', (_req, res) => res.json({ ok: true, ...stats() }));

// --- login page + static -------------------------------------------------- #
app.get('/', (req, res) => {
  // The new mobile-first web IDE is the default app. It reads /api/me itself to
  // decide auth state, so it's served to everyone. Falls back to the legacy
  // login page only if the SPA build isn't present.
  if (SPA_READY) return res.sendFile(SPA_INDEX);
  if (currentSession(req)) return res.redirect('/go');
  res.sendFile(INDEX_HTML);
});
// Legacy email / password / magic-link / anon login page.
app.get('/login', (_req, res) => res.sendFile(INDEX_HTML));
// noVNC desktop Studio (fallback / power users).
app.get('/desktop', (req, res) => res.redirect('/go'));
app.get('/account', (req, res) => {
  if (!currentSession(req)) return res.redirect('/');
  res.sendFile(join(PUBLIC_DIR, 'account.html'));
});
// Single gated entry point to the viewer; picks the UA-appropriate noVNC params
// (mobile vs desktop). Client links point here instead of hardcoding the URL.
app.get('/go', (req, res) => {
  if (!currentSession(req)) return res.redirect('/');
  res.redirect(viewerUrl(req));
});
app.use('/static', express.static(PUBLIC_DIR));

// noVNC's app/ui.js does `fetch('./package.json')` to show its version; the
// packaged noVNC in the container ships without that file, so it 404s and the
// user sees "Couldn't fetch package.json: Error: 404" (cosmetic, but visible on
// mobile). Satisfy it here so the viewer loads clean. Must precede the /app mount.
app.get('/app/package.json', (_req, res) => {
  res.type('application/json').send(JSON.stringify({ name: 'noVNC', version: '1.4.0' }));
});

// --- web IDE backend (gated; GitHub PAT + container agent proxy) ----------- #
// Public web-wallet connect handshake — mounted BEFORE the gated ide router so
// /callback (POSTed by the wallet.animica.org sidecar, no session) isn't 401'd.
app.use('/api/ide/wallet/connect', createWalletConnectRouter());
// Web-wallet one-click sign-and-send funding (CORS callback from the wallet).
app.use('/api/ide/wallet/sign', createWalletSignRouter());

// Mounted before the SPA static/catch-all so /api/ide/* is handled here. The
// router does its own session gating (401 JSON when unauthenticated).
app.use('/api/ide', createIdeRouter({
  currentSession,
  store,
  secrets: ideSecrets,
  github: ideGithub,
  agentProxy: ideAgentProxy,
  sessions,
  proxy, // dev-server preview reverse-proxy (HTTP)
}));

// --- gated reverse-proxy to the user's container (HTTP) -------------------- #
app.use('/app', async (req, res) => {
  const s = currentSession(req);
  if (!s) return res.redirect('/');
  let port;
  try {
    const sess = await ensureSession(s.identity, { tier: s.tier, persistent: s.plan === 'pro' });
    port = sess.port; touch(s.identity);
  } catch {
    return res.status(503).send('Could not start your Studio session. Refresh to retry.');
  }
  proxy.web(req, res, { target: `http://127.0.0.1:${port}` }); // req.url already has /app stripped by the mount
});

// --- new web IDE SPA: static assets + client-side routing fallback --------- #
if (SPA_READY) {
  app.use(express.static(SPA_DIR, { index: false, maxAge: '1h' }));
  app.get('*', (req, res, next) => {
    if (req.method !== 'GET') return next();
    const p = req.path;
    if (p.startsWith('/api') || p.startsWith('/app') || p.startsWith('/static')) return next();
    res.sendFile(SPA_INDEX);
  });
}

// --- server + websocket upgrade gating ------------------------------------- #
const server = http.createServer(app);
server.on('upgrade', async (req, socket, head) => {
  // Path-only (strip query) for prefix routing.
  const path = (req.url || '').split('?')[0];

  // --- web IDE terminal: node-pty PTY over ws (authed, non-anon only) ------- #
  // terminal.js does its own gating + handshake and tolerates node-pty being
  // absent (closes the socket so the UI shows "terminal unavailable").
  if (path === '/api/ide/term' || path.startsWith('/api/ide/term?')) {
    return attachTerminal(req, socket, head, { currentSession, sessions });
  }

  // --- web IDE preview: dev-server ws/HMR proxy (authed only) --------------- #
  // Bridges the iframe's HMR websocket to the container's published DEV port.
  if (path === '/api/ide/preview/app' || path.startsWith('/api/ide/preview/app/')) {
    const s = currentSession(req);
    if (!s) return socket.destroy();
    let devPort;
    try {
      const dev = await ideAgentProxy.getDevBase(s);
      devPort = dev.devPort; touch(s.identity);
    } catch { return socket.destroy(); }
    // Strip the proxy prefix so the dev server sees its own path space.
    req.url = req.url.replace(/^\/api\/ide\/preview\/app/, '') || '/';
    return proxy.ws(req, socket, head, { target: `http://127.0.0.1:${devPort}` });
  }

  // --- noVNC desktop stream (existing) ------------------------------------- #
  if (!path.startsWith('/app')) return socket.destroy();
  const s = currentSession(req);
  if (!s) return socket.destroy();
  let port;
  try {
    const sess = await ensureSession(s.identity, { tier: s.tier, persistent: s.plan === 'pro' });
    port = sess.port; touch(s.identity);
  } catch { return socket.destroy(); }
  req.url = req.url.replace(/^\/app/, '') || '/'; // strip prefix for the container
  proxy.ws(req, socket, head, { target: `http://127.0.0.1:${port}` });
});

startReaper();
server.listen(PORT, '127.0.0.1', () => {
  console.log(`[studio-host] listening on 127.0.0.1:${PORT} | anon=${ANON_ENABLED} magic=${mailerReady()} | image=${config.IMAGE} maxSessions=${config.MAX_SESSIONS} | terminal=${terminalAvailable() ? 'on' : 'unavailable'}`);
});
