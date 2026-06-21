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

import { ensureSession, touch, destroy, startReaper, stats, config } from './sessions.js';
import * as store from './store.js';
import { mailerReady, sendMagicLink } from './mailer.js';
import * as billing from './billing.js';
import * as totp from './totp.js';

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
const ANON_ENABLED = process.env.STUDIO_ALLOW_ANON !== '0';
// noVNC viewer, auto-connecting through the gated /app proxy.
const VIEWER = '/app/vnc.html?autoconnect=true&resize=remote&reconnect=true&path=app/websockify';

const proxy = httpProxy.createProxyServer({ ws: true, xfwd: true });
proxy.on('error', (_e, _req, res) => { try { res.writeHead?.(502); res.end?.('studio backend unavailable'); } catch {} });

const app = express();
app.disable('x-powered-by');
// JSON body for everything EXCEPT the NOWPayments IPN, which needs the raw bytes
// for HMAC signature verification.
app.use((req, res, next) => {
  if (req.path === '/api/billing/nowpayments/ipn') return next();
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
  res.json({ ok: true, redirect: VIEWER });
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
  res.json({ ok: true, redirect: VIEWER });
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
  res.json({ ok: true, redirect: VIEWER });
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
  res.redirect(VIEWER);
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
  if (currentSession(req)) return res.redirect(VIEWER);
  res.sendFile(INDEX_HTML);
});
app.get('/account', (req, res) => {
  if (!currentSession(req)) return res.redirect('/');
  res.sendFile(join(PUBLIC_DIR, 'account.html'));
});
app.use('/static', express.static(PUBLIC_DIR));

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

// --- server + websocket upgrade gating ------------------------------------- #
const server = http.createServer(app);
server.on('upgrade', async (req, socket, head) => {
  if (!req.url.startsWith('/app')) return socket.destroy();
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
  console.log(`[studio-host] listening on 127.0.0.1:${PORT} | anon=${ANON_ENABLED} magic=${mailerReady()} | image=${config.IMAGE} maxSessions=${config.MAX_SESSIONS}`);
});
