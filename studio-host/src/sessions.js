/**
 * Per-user Docker session manager for hosted Animica Studio.
 *
 * Each authenticated identity (wallet address / email user / anonymous id) gets
 * its OWN isolated container running the Studio Qt app + Xvfb + noVNC. This
 * module is the auth-agnostic core: it spawns, tracks, reuses, and reaps those
 * containers and tells the proxy which host port to forward a user to.
 *
 * Isolation per container: non-root, capped memory/cpu/pids, no host mounts
 * (except an optional per-user named volume for persistence), localhost-bound
 * published port, no-new-privileges. Anonymous sessions get a hardened profile.
 */
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const exec = promisify(execFile);

const IMAGE = process.env.STUDIO_IMAGE || 'anm-studio-web';
const IDE_IMAGE = process.env.STUDIO_IDE_IMAGE || 'anm-studio-ide';
const NAME_PREFIX = 'anm-studio-u-';
const IDE_NAME_PREFIX = 'anm-studio-ide-';
const CONTAINER_PORT = '6080';
const AGENT_PORT = '8090';

// Tunables (overridable via env).
const MAX_SESSIONS = parseInt(process.env.STUDIO_MAX_SESSIONS || '12', 10);
const IDLE_MS = parseInt(process.env.STUDIO_IDLE_MS || String(20 * 60 * 1000), 10);
const ANON_TTL_MS = parseInt(process.env.STUDIO_ANON_TTL_MS || String(15 * 60 * 1000), 10);

/** Profiles applied at `docker run` time, keyed by tier. */
const PROFILES = {
  // Paid / authenticated users: more resources, optional persistence.
  standard: { memory: '2g', cpus: '1.5', pids: '400', network: 'bridge' },
  // Anonymous: hardened — fewer resources, short TTL, no persistence. The
  // network stays on so the agent can reach inference, but everything else is
  // clamped down; tighten further (egress allowlist) once policy is decided.
  anon: { memory: '1500m', cpus: '1.0', pids: '300', network: 'bridge', readonlyish: true },
};

/** In-memory registry: key -> { name, port, tier, lastSeen, ttl } */
const sessions = new Map();

function sanitizeKey(key) {
  return String(key).toLowerCase().replace(/[^a-z0-9_-]/g, '').slice(0, 48) || 'anon';
}

async function dockerPort(name, containerPort = CONTAINER_PORT) {
  // `docker port <name> 6080/tcp` -> "127.0.0.1:49153"
  const { stdout } = await exec('docker', ['port', name, `${containerPort}/tcp`]);
  const m = stdout.trim().match(/:(\d+)\s*$/);
  if (!m) throw new Error(`could not read published port for ${name}:${containerPort}`);
  return parseInt(m[1], 10);
}

async function isRunning(name) {
  try {
    const { stdout } = await exec('docker', ['inspect', '-f', '{{.State.Running}}', name]);
    return stdout.trim() === 'true';
  } catch {
    return false;
  }
}

/**
 * Ensure a running session for `key`. Returns { port, name, tier, created }.
 *
 * Two kinds of container can run per identity, tracked under distinct registry
 * keys so they never collide:
 *  - 'web' (default): the noVNC desktop Studio. Published port -> {port}.
 *  - 'ide':           the headless web-IDE container with the FastAPI agent
 *                     sidecar. Published agent port -> {agentPort}.
 *
 * @param {string} key   stable identity (wallet addr / user id / anon id)
 * @param {object} opts  { kind?: 'web'|'ide', tier?: 'standard'|'anon', persistent?: bool }
 */
export async function ensureSession(key, opts = {}) {
  const id = sanitizeKey(key);
  const tier = PROFILES[opts.tier] ? opts.tier : 'standard';
  const ide = opts.kind === 'ide';
  const name = (ide ? IDE_NAME_PREFIX : NAME_PREFIX) + id;
  // Registry key separates the two kinds for the same identity.
  const regKey = ide ? `ide:${id}` : id;

  const existing = sessions.get(regKey);
  if (existing && (await isRunning(name))) {
    existing.lastSeen = Date.now();
    return { ...existing, created: false };
  }

  if (sessions.size >= MAX_SESSIONS) {
    // Try to free an idle slot before refusing.
    await reapIdle();
    if (sessions.size >= MAX_SESSIONS) {
      const err = new Error('Studio is at capacity — please try again shortly.');
      err.code = 'AT_CAPACITY';
      throw err;
    }
  }

  // Clean up any stale container with this name (e.g. after a broker restart).
  await exec('docker', ['rm', '-f', name]).catch(() => {});

  const p = PROFILES[tier];
  const containerPort = ide ? AGENT_PORT : CONTAINER_PORT;
  const args = [
    'run', '-d', '--name', name,
    '--restart', 'no',
    '-p', `127.0.0.1::${containerPort}`,
    '--memory', p.memory, '--memory-swap', p.memory,
    '--cpus', p.cpus, '--pids-limit', p.pids,
    '--security-opt', 'no-new-privileges',
    '--label', 'anm-studio=1',
    '--label', `anm-tier=${tier}`,
    '--label', `anm-kind=${ide ? 'ide' : 'web'}`,
  ];
  if (ide) {
    // Headless sidecar: drop all Linux caps (it only needs git+fs), keep
    // no-new-privileges, hand it the agent port, and give it a WRITABLE named
    // volume for the cloned repo (the agent owns /home/studio/workspace).
    args.push('--cap-drop', 'ALL', '-e', `AGENT_PORT=${AGENT_PORT}`);
    // ENA inference config for the sidecar's agent loop (key-agnostic: if the key
    // is empty the sidecar streams a clean "not configured" SSE error instead of
    // crashing). Defaults point at the live pool broker.
    args.push('-e', 'ANIMICA_ENA_BASE=' + (process.env.STUDIO_ENA_BASE || 'https://pool.animica.org/v1'));
    args.push('-e', 'ANIMICA_ENA_KEY=' + (process.env.STUDIO_ENA_KEY || ''));
    args.push('-e', 'ANIMICA_ENA_MODEL=' + (process.env.STUDIO_ENA_MODEL || 'anm-code-7b'));
    args.push('-v', `anm-ide-vol-${id}:/home/studio/workspace`);
    args.push(IDE_IMAGE);
  } else {
    if (p.readonlyish) {
      // Keep the rootfs read-only (security) but the writable tmpfs overlays MUST
      // be owned by the in-container `studio` user (uid/gid 10001) — otherwise the
      // home tmpfs is root-owned and the Studio app can't create its config dir
      // (/home/studio/.local/share/...) and crashes on launch.
      args.push(
        '--read-only',
        '--tmpfs', '/tmp:exec,size=512m,uid=10001,gid=10001,mode=1777',
        '--tmpfs', '/home/studio:exec,size=512m,uid=10001,gid=10001,mode=0775',
      );
    }
    if (opts.persistent && tier !== 'anon') {
      args.push('-v', `anm-studio-vol-${id}:/home/studio/workspace`);
    }
    args.push(IMAGE);
  }

  await exec('docker', args);
  const port = await dockerPort(name, containerPort);
  const rec = {
    name, tier, kind: ide ? 'ide' : 'web',
    lastSeen: Date.now(),
    ttl: tier === 'anon' ? ANON_TTL_MS : IDLE_MS,
  };
  if (ide) rec.agentPort = port; else rec.port = port;
  sessions.set(regKey, rec);
  return { ...rec, created: true };
}

export function touch(key) {
  const rec = sessions.get(sanitizeKey(key));
  if (rec) rec.lastSeen = Date.now();
}

export async function destroy(key) {
  const id = sanitizeKey(key);
  const rec = sessions.get(id);
  const name = rec ? rec.name : NAME_PREFIX + id;
  await exec('docker', ['rm', '-f', name]).catch(() => {});
  sessions.delete(id);
}

/** Stop containers idle longer than their TTL. Returns count reaped. */
export async function reapIdle() {
  const now = Date.now();
  let reaped = 0;
  for (const [id, rec] of sessions) {
    if (now - rec.lastSeen > rec.ttl) {
      await exec('docker', ['rm', '-f', rec.name]).catch(() => {});
      sessions.delete(id);
      reaped++;
    }
  }
  return reaped;
}

export function stats() {
  return {
    active: sessions.size,
    max: MAX_SESSIONS,
    sessions: [...sessions.values()].map((s) => ({ tier: s.tier, idleMs: Date.now() - s.lastSeen })),
  };
}

// Background reaper.
let timer = null;
export function startReaper(intervalMs = 60_000) {
  if (timer) return;
  timer = setInterval(() => { reapIdle().catch(() => {}); }, intervalMs);
  if (timer.unref) timer.unref();
}

export const config = { IMAGE, IDE_IMAGE, MAX_SESSIONS, IDLE_MS, ANON_TTL_MS, PROFILES };
