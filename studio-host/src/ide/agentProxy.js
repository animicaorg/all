/**
 * Proxy helper: broker session -> the user's IDE container "agent sidecar".
 *
 * The sidecar is a headless FastAPI app inside the per-user container, listening
 * on the published agent port (container 8090). We resolve/spawn that container
 * via sessions.ensureSession(identity, {kind:'ide'}) and forward JSON requests to
 * http://127.0.0.1:<agentPort><path>. Everything stays bound to localhost.
 */
import { ensureSession } from '../sessions.js';

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
