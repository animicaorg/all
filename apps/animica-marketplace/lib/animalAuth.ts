import { createHmac, createHash, scryptSync, timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import { prisma } from './db';
import { config } from './config';

// Operator console auth for Animica Animal. This is a single-operator password gate (separate from
// wallet login) protecting the mascot's connect-socials + steering console. Password is verified
// against a scrypt hash — the plaintext is never stored. Attempts are rate-limited with a durable
// per-IP lockout plus a global backstop, so the gate resists online guessing across restarts.

const CONSOLE_USER = process.env.ANIMAL_CONSOLE_USER || 'animica';
// scrypt$N$r$p$saltHex$hashHex — default is the hash of the operator-provided password (overridable).
const PWHASH =
  process.env.ANIMAL_CONSOLE_PWHASH ||
  'scrypt$16384$8$1$b020a4b235d50485a92de49454467c8d$b7634bb1a2fb3a11cde12d3d25eee23f663e43a7fb7a64203738f3cde8ac7a71';

const COOKIE = 'anm_animal_console';
const MAX_AGE = 60 * 60 * 12; // 12h operator session

// Throttle policy: up to MAX_FAILS bad attempts per IP inside WINDOW, then a LOCK. A global bucket
// caps distributed guessing without letting one attacker lock the real operator out permanently.
const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILS_IP = 5;
const LOCK_MS = 15 * 60 * 1000;
const MAX_FAILS_GLOBAL = 60;

function ipBucket(ip: string): string {
  return 'ip:' + createHash('sha256').update(ip || 'unknown').digest('hex').slice(0, 32);
}

function verifyScrypt(password: string, encoded: string): boolean {
  try {
    const [scheme, N, r, p, saltHex, hashHex] = encoded.split('$');
    if (scheme !== 'scrypt') return false;
    const salt = Buffer.from(saltHex, 'hex');
    const expected = Buffer.from(hashHex, 'hex');
    const got = scryptSync(password, salt, expected.length, {
      N: Number(N), r: Number(r), p: Number(p), maxmem: 64 * 1024 * 1024,
    });
    return got.length === expected.length && timingSafeEqual(got, expected);
  } catch {
    return false;
  }
}

export interface ThrottleState {
  allowed: boolean;
  retryAfter: number; // seconds until unlock (0 if allowed)
  reason?: string;
}

// checkThrottle only needs to know whether an ACTIVE lock is in force right now. A lock whose
// lockedUntil is in the past is NOT active — even if the stale fails counter is still high — so an
// expired lock never keeps the operator out (that was the permanent-DoS bug). The counter/window
// is properly reset on the next failed attempt inside bump().
export async function checkThrottle(ip: string): Promise<ThrottleState> {
  const now = Date.now();
  for (const bucket of [ipBucket(ip), 'global']) {
    const row = await prisma.consoleThrottle.findUnique({ where: { bucket } });
    if (row?.lockedUntil && row.lockedUntil.getTime() > now) {
      return {
        allowed: false,
        retryAfter: Math.ceil((row.lockedUntil.getTime() - now) / 1000),
        reason: bucket === 'global' ? 'too many attempts (global)' : 'too many attempts',
      };
    }
  }
  return { allowed: true, retryAfter: 0 };
}

async function bump(bucket: string, ok: boolean, limit: number) {
  const now = new Date();
  if (ok) {
    // Success clears the bucket entirely (fresh window, no lock).
    await prisma.consoleThrottle.upsert({
      where: { bucket },
      create: { bucket, fails: 0, windowStart: now, lockedUntil: null },
      update: { fails: 0, windowStart: now, lockedUntil: null },
    });
    return;
  }

  // Ensure the row exists (unique bucket serializes concurrent creators).
  await prisma.consoleThrottle.upsert({
    where: { bucket },
    create: { bucket, fails: 0, windowStart: now, lockedUntil: null },
    update: {},
  });

  // If the previous window OR a previous lock has fully elapsed, start a FRESH window before
  // counting — so an expired lock resets to 0 instead of re-locking on the first new failure.
  const row = await prisma.consoleThrottle.findUnique({ where: { bucket } });
  if (row) {
    const lockElapsed = !row.lockedUntil || row.lockedUntil.getTime() <= now.getTime();
    const windowElapsed = now.getTime() - row.windowStart.getTime() > WINDOW_MS;
    if (lockElapsed && (windowElapsed || row.lockedUntil)) {
      await prisma.consoleThrottle.update({
        where: { bucket }, data: { fails: 0, windowStart: now, lockedUntil: null },
      });
    }
  }

  // Atomic DB-side increment: N concurrent failures each count (defeats the read-modify-write race).
  const updated = await prisma.consoleThrottle.update({
    where: { bucket }, data: { fails: { increment: 1 } },
  });
  const activeLock = updated.lockedUntil && updated.lockedUntil.getTime() > now.getTime();
  if (updated.fails >= limit && !activeLock) {
    await prisma.consoleThrottle.update({
      where: { bucket }, data: { lockedUntil: new Date(now.getTime() + LOCK_MS) },
    });
  }
}

export async function recordAttempt(ip: string, ok: boolean) {
  await bump(ipBucket(ip), ok, MAX_FAILS_IP);
  await bump('global', ok, MAX_FAILS_GLOBAL);
}

// Verify credentials WITHOUT touching the throttle (callers gate with checkThrottle + recordAttempt).
export function verifyCredentials(user: string, password: string): boolean {
  const userOk = (user || '') === CONSOLE_USER;
  const passOk = verifyScrypt(password || '', PWHASH);
  // Always run scrypt to keep timing uniform regardless of username correctness.
  return userOk && passOk;
}

// ── console session cookie (HMAC-signed, distinct from wallet session) ─────────
// The token binds a server-side EPOCH (stored in AnimalEngineState.consoleEpoch). Logout bumps the
// epoch, which invalidates every outstanding token immediately — so a leaked/stolen token stops
// working the moment the operator signs out, not 12h later.
const ENGINE_ID = 'animal';

function sign(payload: string): string {
  return createHmac('sha256', config.sessionSecret + '|animal-console').update(payload).digest('base64url');
}

async function currentEpoch(): Promise<number> {
  const st = await prisma.animalEngineState.findUnique({ where: { id: ENGINE_ID } });
  return st?.consoleEpoch ?? 1;
}

export async function bumpConsoleEpoch(): Promise<void> {
  await prisma.animalEngineState.upsert({
    where: { id: ENGINE_ID },
    create: { id: ENGINE_ID, consoleEpoch: 2 },
    update: { consoleEpoch: { increment: 1 } },
  });
}

export async function issueConsoleSession(): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + MAX_AGE;
  const epoch = await currentEpoch();
  const payload = `console.${epoch}.${exp}`;
  return `${payload}.${sign(payload)}`;
}

// Verify signature + expiry; return the embedded epoch (or null). Epoch equality is checked by the
// async caller against the live server epoch.
function parseConsoleToken(token: string | undefined): { epoch: number; exp: number } | null {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length !== 4 || parts[0] !== 'console') return null;
  const epoch = Number(parts[1]);
  const exp = Number(parts[2]);
  if (!Number.isFinite(epoch) || !Number.isFinite(exp)) return null;
  const expected = sign(`console.${parts[1]}.${parts[2]}`);
  try {
    if (!timingSafeEqual(Buffer.from(parts[3]), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  if (exp < Math.floor(Date.now() / 1000)) return null;
  return { epoch, exp };
}

export async function setConsoleCookie() {
  cookies().set(COOKIE, await issueConsoleSession(), {
    httpOnly: true, secure: true, sameSite: 'lax', path: '/', maxAge: MAX_AGE,
  });
}

export function clearConsoleCookie() {
  cookies().delete(COOKIE);
}

export async function isConsoleAuthed(): Promise<boolean> {
  const parsed = parseConsoleToken(cookies().get(COOKIE)?.value);
  if (!parsed) return false;
  return parsed.epoch === (await currentEpoch());
}

export const CONSOLE_USERNAME = CONSOLE_USER;
