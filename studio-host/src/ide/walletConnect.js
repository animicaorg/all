/**
 * Web-wallet connect handshake (wallet.animica.org) for the Studio IDE.
 *
 * Ports the protocol apps/animica-xyz uses:
 *   1. POST /start  → make a requestId + nonce, build the popup URL
 *        https://wallet.animica.org/connect/?request=<b64url(JSON{payload})>&id=<requestId>
 *      The client window.open()s it.
 *   2. POST /callback ← the wallet.animica.org sidecar POSTs the user's
 *      approval here (server-to-server, NO browser session). The requestId is a
 *      server-generated UUID only ever exposed via the popup URL, so we trust it.
 *   3. GET /poll?requestId= → the client polls until approved/rejected/expired.
 *
 * No pre-shared key: the user visually approves the requesting origin in the web
 * wallet. This router is PUBLIC (mounted outside the IDE session gate) precisely
 * because the wallet sidecar — not the user's browser — calls /callback.
 */
import express from 'express';
import { randomUUID, randomBytes } from 'node:crypto';

const TTL_MS = 5 * 60 * 1000;

function b64url(obj) {
  return Buffer.from(JSON.stringify(obj)).toString('base64url');
}
const isAnmAddr = (a) => typeof a === 'string' && /^anim1[0-9a-z]{20,}$/.test(a);

export function createWalletConnectRouter() {
  const router = express.Router();
  const reqs = new Map(); // requestId -> { nonce, status, expiresAt, address, accounts }

  const WALLET_URL = (process.env.STUDIO_WALLET_URL || 'https://wallet.animica.org').replace(/\/+$/, '');
  const APP_URL = (process.env.STUDIO_PUBLIC_BASE || 'https://studio.animica.org').replace(/\/+$/, '');
  const CHAIN_ID = Number(process.env.STUDIO_ANM_CHAIN_ID || '1') || 1;

  // Drop expired requests lazily + on a light interval.
  function sweep() {
    const now = Date.now();
    for (const [id, r] of reqs) if (r.expiresAt <= now && r.status === 'pending') r.status = 'expired';
    for (const [id, r] of reqs) if (r.expiresAt + TTL_MS <= now) reqs.delete(id);
  }

  router.post('/start', express.json(), (_req, res) => {
    sweep();
    const requestId = randomUUID();
    const nonce = randomBytes(32).toString('hex');
    const ts = Math.floor(Date.now() / 1000);
    const callback = `${APP_URL}/api/ide/wallet/connect/callback`;
    const payload = {
      app: 'Animica Studio',
      origin: APP_URL,
      chainId: CHAIN_ID,
      scopes: ['accounts'],
      ts,
      callback,
      nonce,
    };
    const expiresAt = Date.now() + TTL_MS;
    reqs.set(requestId, { nonce, status: 'pending', expiresAt, address: null, accounts: [] });
    const popupUrl = `${WALLET_URL}/connect/?request=${b64url({ payload })}&id=${encodeURIComponent(requestId)}`;
    res.json({ requestId, popupUrl, expiresAt: new Date(expiresAt).toISOString() });
  });

  router.post('/callback', express.json(), (req, res) => {
    const { requestId, approved, accounts, nonceSignature, sessionToken } = req.body || {};
    if (!requestId || typeof approved !== 'boolean' || !Array.isArray(accounts)) {
      return res.status(400).json({ error: 'requestId, approved, accounts required' });
    }
    const r = reqs.get(requestId);
    if (!r) return res.status(404).json({ error: 'Unknown requestId' });
    if (r.status !== 'pending') return res.status(409).json({ error: `already ${r.status}` });
    if (r.expiresAt <= Date.now()) { r.status = 'expired'; return res.status(410).json({ error: 'expired' }); }
    if (approved) {
      for (const a of accounts) if (!isAnmAddr(a)) return res.status(400).json({ error: `invalid account: ${a}` });
    }
    r.status = approved ? 'approved' : 'rejected';
    r.accounts = approved ? accounts : [];
    r.address = approved ? accounts[0] || null : null;
    r.nonceSignature = nonceSignature || null;
    r.sessionToken = sessionToken || null;
    res.json({ ok: true });
  });

  router.get('/poll', (req, res) => {
    sweep();
    const id = String(req.query.requestId || '');
    const r = reqs.get(id);
    if (!r) return res.status(404).json({ status: 'unknown' });
    if (r.status === 'approved') return res.json({ status: 'approved', address: r.address, accounts: r.accounts });
    res.json({ status: r.status });
  });

  return router;
}
