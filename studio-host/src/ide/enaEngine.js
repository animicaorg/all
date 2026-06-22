/**
 * ENA inference engine proxy → the AICF chat-bridge (ANM-native, no credits).
 *
 * The bridge (default http://127.0.0.1:4600) is localhost-only and accepts any
 * key (it pays for inference on-chain from the operator's `aicf` wallet via AICF
 * workers). The studio's per-user containers can't reach localhost, and we must
 * not expose free inference publicly — so this gated proxy sits on the broker
 * (which CAN reach the bridge) and forwards only requests bearing the broker's
 * own STUDIO_ENA_ENGINE_SECRET (injected into each container as ANIMICA_ENA_KEY).
 *
 * Mounted OUTSIDE the IDE session gate (the sidecar has no browser session); the
 * shared secret is the gate. Streams responses (SSE or JSON) straight through.
 */
import express from 'express';

export function createEnaEngineRouter() {
  const router = express.Router();
  const BRIDGE = (process.env.STUDIO_ENA_BRIDGE_UPSTREAM || 'http://127.0.0.1:4600').replace(/\/+$/, '');
  const SECRET = process.env.STUDIO_ENA_ENGINE_SECRET || '';

  function gated(req, res) {
    const a = req.headers.authorization || '';
    if (!SECRET || a !== `Bearer ${SECRET}`) {
      res.status(401).json({ error: { message: 'unauthorized engine' } });
      return false;
    }
    return true;
  }

  // Public model list (handy for probes); no secret required.
  router.get('/v1/models', async (_req, res) => {
    try {
      const u = await fetch(`${BRIDGE}/v1/models`);
      res.status(u.status).set('content-type', 'application/json').send(await u.text());
    } catch {
      res.status(502).json({ error: { message: 'engine bridge unavailable' } });
    }
  });

  // Chat/completions → bridge. Pipe the response through (SSE or JSON).
  router.post(['/v1/chat/completions', '/v1/completions', '/v1/embeddings'], express.json({ limit: '8mb' }), async (req, res) => {
    if (!gated(req, res)) return;
    let u;
    try {
      u = await fetch(`${BRIDGE}${req.path}`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(req.body || {}),
      });
    } catch {
      return res.status(502).json({ error: { message: 'engine bridge unavailable' } });
    }
    res.status(u.status);
    const ct = u.headers.get('content-type');
    if (ct) res.set('content-type', ct);
    res.set('cache-control', 'no-cache, no-transform');
    res.set('x-accel-buffering', 'no');
    if (!u.body) {
      res.end(await u.text().catch(() => ''));
      return;
    }
    const reader = u.body.getReader();
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        res.write(Buffer.from(value));
        if (typeof res.flush === 'function') res.flush();
      }
    } catch {
      /* client/bridge dropped */
    }
    res.end();
  });

  return router;
}
