/**
 * Web IDE terminal bridge — a node-pty PTY over a WebSocket.
 *
 * The broker's server.on('upgrade') hands us the raw socket for the
 * '/api/ide/term' path. We complete the WS handshake here (a `ws`
 * WebSocketServer in noServer mode), gate the session (authenticated, NON-anon),
 * ensure the user's IDE container is running, and bridge a PTY running an
 * interactive login shell INSIDE that container:
 *
 *     docker exec -it -u studio -w /home/studio/workspace/repo <name> /bin/bash -l
 *
 * Wire protocol (matches the studio-ide termSocket client):
 *   client -> pty : text frames are raw stdin bytes;
 *                   a JSON control frame {type:'resize',cols,rows} resizes the pty.
 *   pty    -> client : pty output is written as text frames.
 *
 * The pty is killed on ws close, and an idle timeout (~15min, reset on any
 * client activity) tears the whole thing down.
 *
 * node-pty ships a native addon. If it failed to build on this host the import
 * throws; we tolerate that and close the socket cleanly so the UI can show
 * "terminal unavailable" instead of the broker failing to boot.
 */
import { WebSocketServer } from 'ws';

// Lazily resolve node-pty so a missing/failed native build never crashes the
// broker at import time. `ptyMod` is null when unavailable.
let ptyMod = null;
let ptyLoadError = null;
try {
  ptyMod = (await import('node-pty')).default || (await import('node-pty'));
} catch (e) {
  ptyLoadError = e;
  ptyMod = null;
}

/** Whether the terminal feature can run (native addon loaded). */
export function terminalAvailable() {
  return !!(ptyMod && typeof ptyMod.spawn === 'function');
}

// One shared WebSocketServer in noServer mode; we drive the handshake manually
// from the upgrade handler. (noServer => it never binds its own http.Server.)
const wss = new WebSocketServer({ noServer: true });

const IDLE_MS = 15 * 60 * 1000;

/**
 * Bridge a container shell PTY to a WebSocket for the '/api/ide/term' upgrade.
 *
 * @param {import('http').IncomingMessage} req
 * @param {import('stream').Duplex} socket  the raw TCP socket from 'upgrade'
 * @param {Buffer} head
 * @param {object} deps
 * @param {(req)=>object|null} deps.currentSession  resolve broker session
 * @param {object} deps.sessions                    sessions.js (ensureSession/touch)
 */
export function attachTerminal(req, socket, head, { currentSession, sessions }) {
  const s = currentSession(req);
  // Gate: must be signed in AND not anonymous (terminal is authed-only).
  if (!s || s.tier === 'anon') {
    closeRaw(socket, 1008, 'terminal requires sign-in');
    return;
  }
  if (!terminalAvailable()) {
    closeRaw(socket, 1011, 'terminal unavailable');
    return;
  }

  wss.handleUpgrade(req, socket, head, (ws) => {
    runShell(ws, s, sessions).catch((e) => {
      try { ws.close(1011, (e && e.message ? e.message : 'terminal error').slice(0, 120)); } catch {}
    });
  });
}

async function runShell(ws, session, sessions) {
  // Ensure the IDE container is up and get its name. Persist authed users' repos.
  let name;
  try {
    const rec = await sessions.ensureSession(session.identity, {
      kind: 'ide', tier: session.tier, persistent: session.tier !== 'anon',
    });
    name = rec.name;
    sessions.touch(session.identity);
  } catch (e) {
    try { ws.close(1011, 'could not start IDE session'); } catch {}
    return;
  }
  if (ws.readyState !== ws.OPEN) return; // client gave up during cold start

  // Spawn an interactive login bash INSIDE the container as the non-root studio
  // user, working in the cloned repo dir. `-it` gives it a tty (pty allocation).
  const pty = ptyMod.spawn('docker', [
    'exec', '-it',
    '-u', 'studio',
    '-w', '/home/studio/workspace/repo',
    name,
    '/bin/bash', '-l',
  ], {
    name: 'xterm-color',
    cols: 80,
    rows: 24,
    cwd: process.cwd(),
    env: process.env,
  });

  let closed = false;
  const cleanup = () => {
    if (closed) return;
    closed = true;
    clearTimeout(idleTimer);
    try { pty.kill(); } catch {}
    try { ws.close(); } catch {}
  };

  // Idle timeout: reset on ANY client message (input/resize); fires after 15min
  // of no client activity.
  let idleTimer = null;
  const bumpIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      try { ws.close(4000, 'idle timeout'); } catch {}
      cleanup();
    }, IDLE_MS);
  };
  bumpIdle();

  // pty -> client (text frames).
  pty.onData((data) => {
    if (ws.readyState === ws.OPEN) {
      try { ws.send(data); } catch {}
    }
  });
  pty.onExit(() => {
    if (!closed) { try { ws.close(1000, 'shell exited'); } catch {} }
    cleanup();
  });

  // client -> pty. A JSON {type:'resize'} control frame resizes; anything else
  // is raw stdin.
  ws.on('message', (raw, isBinary) => {
    sessions.touch(session.identity);
    bumpIdle();
    const text = isBinary ? raw.toString('utf8') : raw.toString();
    if (text && text[0] === '{') {
      try {
        const msg = JSON.parse(text);
        if (msg && msg.type === 'resize') {
          const cols = Math.max(1, Math.min(1000, parseInt(msg.cols, 10) || 80));
          const rows = Math.max(1, Math.min(1000, parseInt(msg.rows, 10) || 24));
          try { pty.resize(cols, rows); } catch {}
          return;
        }
      } catch {
        // Not JSON control — fall through and treat as stdin.
      }
    }
    try { pty.write(text); } catch {}
  });

  ws.on('close', cleanup);
  ws.on('error', cleanup);
}

/** Reply to a raw (un-upgraded) socket with a minimal WS close, then destroy. */
function closeRaw(socket, _code, reason) {
  // The handshake hasn't completed, so we can't send a WS close frame. Send a
  // 1011-style HTTP rejection that the browser surfaces as a failed upgrade; the
  // client then renders "terminal unavailable".
  try {
    socket.write(
      'HTTP/1.1 503 Service Unavailable\r\n' +
      'Connection: close\r\n' +
      'Content-Type: text/plain\r\n' +
      `X-Terminal-Error: ${(reason || 'unavailable').replace(/[\r\n]/g, ' ')}\r\n` +
      '\r\n' +
      (reason || 'terminal unavailable'),
    );
  } catch {}
  try { socket.destroy(); } catch {}
}

export default attachTerminal;
