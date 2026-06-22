/**
 * Proxy helper: broker session -> the user's IDE container "agent sidecar".
 *
 * The sidecar is a headless FastAPI app inside the per-user container, listening
 * on the published agent port (container 8090). We resolve/spawn that container
 * via sessions.ensureSession(identity, {kind:'ide'}) and forward JSON requests to
 * http://127.0.0.1:<agentPort><path>. Everything stays bound to localhost.
 */
import { ensureSession } from '../sessions.js';
import { Readable } from 'node:stream';

/**
 * Ensure the caller's IDE container is running and return its agent base URL.
 * @param {object} session  broker session ({identity, tier, plan, ...})
 * @returns {Promise<{base:string, name:string, agentPort:number}>}
 */
export async function getAgentBase(session) {
  const persistent = session.tier !== 'anon'; // keep authed users' repos across idle
  const rec = await ensureSession(session.identity, { kind: 'ide', tier: session.tier, persistent });
  const port = rec.agentPort;
  if (!port) throw Object.assign(new Error('ide container has no agent port'), { status: 502 });
  return { base: `http://127.0.0.1:${port}`, name: rec.name, agentPort: port };
}

/**
 * Forward a JSON request to the session's sidecar and return parsed JSON.
 * Throws Error with {status, body} on a non-2xx response so routes can relay it.
 * @param {object} session
 * @param {string} method   'GET'|'POST'|'PUT'|...
 * @param {string} path     sidecar path beginning with '/', may include query
 * @param {object} [body]   JSON body for write methods
 */
export async function forwardJson(session, method, path, body) {
  const { base } = await getAgentBase(session);
  const init = { method, headers: { accept: 'application/json' } };
  if (body !== undefined && body !== null) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  // A just-spawned container needs ~1-2s before uvicorn accepts connections, so
  // retry connection failures briefly (covers the cold-start race on the first
  // call after /repo/open spawns the IDE container).
  let res;
  const maxAttempts = 9;
  for (let attempt = 1; ; attempt++) {
    try {
      res = await fetch(`${base}${path}`, init);
      break;
    } catch (e) {
      if (attempt >= maxAttempts) {
        throw Object.assign(new Error('sidecar unreachable'), { status: 502, body: { error: 'IDE backend unavailable.' } });
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  let parsed = null;
  try { parsed = await res.json(); } catch { parsed = null; }
  if (!res.ok) {
    throw Object.assign(new Error('sidecar error'), {
      status: res.status,
      body: parsed || { error: `IDE backend error (${res.status}).` },
    });
  }
  return parsed;
}

/**
 * Stream-proxy a request to the session's sidecar, piping the upstream response
 * body straight through to the express `res` as Server-Sent Events. Used for the
 * long-lived ENA chat stream so events flush to the browser as they arrive
 * (no buffering anywhere in the chain).
 *
 * - Sets SSE response headers + disables proxy buffering (X-Accel-Buffering).
 * - Applies the same cold-start connect retry as forwardJson.
 * - Aborts the upstream fetch when the client disconnects (res 'close').
 * - On a non-2xx upstream, emits a single clean SSE `error` event then ends,
 *   so the EventSource/reader on the client sees a graceful failure.
 *
 * @param {object} session
 * @param {string} method   'POST' (typical) | 'GET' | ...
 * @param {string} path     sidecar path beginning with '/'
 * @param {object|null} body  JSON body for write methods
 * @param {import('express').Request} req
 * @param {import('express').Response} res
 */
export async function streamProxy(session, method, path, body, req, res) {
  const { base } = await getAgentBase(session);
  const ac = new AbortController();
  // Abort the upstream as soon as the browser hangs up so the worker thread in
  // the sidecar can tear down its loop instead of streaming into the void.
  const onClose = () => { try { ac.abort(); } catch {} };
  req.on('close', onClose);
  res.on('close', onClose);

  const init = {
    method,
    headers: { accept: 'text/event-stream' },
    signal: ac.signal,
  };
  if (body !== undefined && body !== null) {
    init.headers['content-type'] = 'application/json';
    init.body = JSON.stringify(body);
  }

  // SSE response headers — flush them immediately and defeat any buffering.
  res.statusCode = 200;
  res.setHeader('Content-Type', 'text/event-stream; charset=utf-8');
  res.setHeader('Cache-Control', 'no-cache, no-transform');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  if (typeof res.flushHeaders === 'function') res.flushHeaders();

  // Emit a single SSE event frame to the client.
  const sendEvent = (type, data) => {
    try {
      res.write(`event: ${type}\ndata: ${JSON.stringify(data)}\n\n`);
    } catch {}
  };

  // Connect with the cold-start retry (a freshly spawned container needs ~1-2s).
  let upstream;
  const maxAttempts = 9;
  for (let attempt = 1; ; attempt++) {
    if (ac.signal.aborted) { try { res.end(); } catch {} return; }
    try {
      upstream = await fetch(`${base}${path}`, init);
      break;
    } catch (e) {
      if (ac.signal.aborted) { try { res.end(); } catch {} return; }
      if (attempt >= maxAttempts) {
        sendEvent('error', { message: 'IDE backend unavailable.' });
        try { res.end(); } catch {}
        return;
      }
      await new Promise((r) => setTimeout(r, 500));
    }
  }

  if (!upstream.ok || !upstream.body) {
    // Surface the sidecar error as a single SSE error event (the body may be
    // JSON like {error} or empty); never leave the stream hanging.
    let detail = `IDE backend error (${upstream.status}).`;
    try {
      const txt = await upstream.text();
      if (txt) {
        try { detail = JSON.parse(txt).error || detail; } catch { detail = txt.slice(0, 500); }
      }
    } catch {}
    sendEvent('error', { message: detail });
    try { res.end(); } catch {}
    return;
  }

  // Pipe the upstream web ReadableStream straight to res as chunks arrive.
  const nodeStream = Readable.fromWeb(upstream.body);
  nodeStream.on('error', () => {
    sendEvent('error', { message: 'IDE backend stream error.' });
    try { res.end(); } catch {}
  });
  nodeStream.on('end', () => { try { res.end(); } catch {} });
  // When the client disconnects, destroy the node stream too (the AbortController
  // already cancels the underlying fetch).
  res.on('close', () => { try { nodeStream.destroy(); } catch {} });
  nodeStream.pipe(res, { end: false });
}
