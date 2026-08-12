import { createHmac, createHash, randomBytes, scryptSync, timingSafeEqual } from 'node:crypto';
import { cookies } from 'next/headers';
import type { NextRequest } from 'next/server';
import { prisma } from './db';
import { config } from './config';
import { ApiError } from './api';

// Auth for the Hire Me surfaces. Two principals, both HMAC-cookie sessions signed with
// SESSION_SECRET (domain-scoped so they work on animica.dev AND the admin./dashboard. subdomains):
//   • operator/admin — single login from env (HIRE_ADMIN_USER + scrypt HIRE_ADMIN_PWHASH),
//     cookie anm_hire_admin. The x-admin-token (MKT_ADMIN_TOKEN) header also passes, so the
//     store-review token workflow and CLI curl keep working.
//   • customer — HireCustomer rows (email + scrypt password), cookie anm_hire_customer bound to
//     the row's sessionEpoch (logout bumps the epoch => every outstanding token dies).
// Login attempts are throttled via the durable ConsoleThrottle table under 'hire:' buckets.

const ADMIN_USER = process.env.HIRE_ADMIN_USER || '';
const ADMIN_PWHASH = process.env.HIRE_ADMIN_PWHASH || ''; // scrypt$N$r$p$saltHex$hashHex; empty => admin login disabled
const COOKIE_DOMAIN = process.env.HIRE_COOKIE_DOMAIN || undefined; // '.animica.dev' in production

const ADMIN_COOKIE = 'anm_hire_admin';
const CUSTOMER_COOKIE = 'anm_hire_customer';
const ADMIN_MAX_AGE = 60 * 60 * 12; // 12h
const CUSTOMER_MAX_AGE = 60 * 60 * 24 * 30; // 30 days

// ── scrypt password hashing (same encoded format as the Animal console) ──────
// Encoded as scrypt:N:r:p:saltHex:hashHex — deliberately '$'-FREE. The operator hash lives in
// .env.production, and a '$' there is destroyed twice over: systemd's EnvironmentFile parser eats
// it, and Next.js loads the same file through dotenv-expand, which expands `$16384` as a variable
// reference. Legacy '$'-separated hashes (the Animal console format) still verify.
export function hashPassword(password: string): string {
  const N = 16384, r = 8, p = 1;
  const salt = randomBytes(16);
  const hash = scryptSync(password, salt, 32, { N, r, p, maxmem: 64 * 1024 * 1024 });
  return `scrypt:${N}:${r}:${p}:${salt.toString('hex')}:${hash.toString('hex')}`;
}

export function verifyPassword(password: string, encoded: string): boolean {
  try {
    const [scheme, N, r, p, saltHex, hashHex] = encoded.includes(':') ? encoded.split(':') : encoded.split('$');
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

// A constant dummy hash so login timing is uniform whether or not the email exists.
const DUMMY_HASH = hashPassword(randomBytes(16).toString('hex'));

// ── durable login throttle (ConsoleThrottle table, 'hire:' namespace) ────────
const WINDOW_MS = 15 * 60 * 1000;
const MAX_FAILS_IP = 8;
const LOCK_MS = 15 * 60 * 1000;
const MAX_FAILS_GLOBAL = 120;

const ipBucket = (ip: string) =>
  'hire:ip:' + createHash('sha256').update(ip || 'unknown').digest('hex').slice(0, 32);
const GLOBAL_BUCKET = 'hire:global';

export async function checkHireThrottle(ip: string): Promise<{ allowed: boolean; retryAfter: number }> {
  const now = Date.now();
  for (const bucket of [ipBucket(ip), GLOBAL_BUCKET]) {
    const row = await prisma.consoleThrottle.findUnique({ where: { bucket } });
    if (row?.lockedUntil && row.lockedUntil.getTime() > now) {
      return { allowed: false, retryAfter: Math.ceil((row.lockedUntil.getTime() - now) / 1000) };
    }
  }
  return { allowed: true, retryAfter: 0 };
}

async function bump(bucket: string, ok: boolean, limit: number) {
  const now = new Date();
  // NOTE: success deliberately does NOT clear the counters. Registration and login share these
  // buckets, so "success resets the bucket" would let an attacker interleave a self-registration
  // (or a login to their own account) between guesses and never hit the lockout at all. Counters
  // simply age out with the 15-minute window instead.
  if (ok) return;
  await prisma.consoleThrottle.upsert({
    where: { bucket },
    create: { bucket, fails: 0, windowStart: now, lockedUntil: null },
    update: {},
  });
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

export async function recordHireAttempt(ip: string, ok: boolean) {
  await bump(ipBucket(ip), ok, MAX_FAILS_IP);
  await bump(GLOBAL_BUCKET, ok, MAX_FAILS_GLOBAL);
}

// ── HMAC session tokens ──────────────────────────────────────────────────────
function sign(scope: string, payload: string): string {
  return createHmac('sha256', config.sessionSecret + '|' + scope).update(payload).digest('base64url');
}

function parseToken(scope: string, prefix: string, token: string | undefined, parts: number):
  string[] | null {
  if (!token) return null;
  const seg = token.split('.');
  if (seg.length !== parts || seg[0] !== prefix) return null;
  const payload = seg.slice(0, -1).join('.');
  const expected = sign(scope, payload);
  try {
    if (!timingSafeEqual(Buffer.from(seg[seg.length - 1]), Buffer.from(expected))) return null;
  } catch {
    return null;
  }
  const exp = Number(seg[seg.length - 2]);
  if (!Number.isFinite(exp) || exp < Math.floor(Date.now() / 1000)) return null;
  return seg;
}

const cookieOpts = (maxAge: number) => ({
  httpOnly: true as const,
  secure: true as const,
  sameSite: 'lax' as const,
  path: '/' as const,
  maxAge,
  ...(COOKIE_DOMAIN ? { domain: COOKIE_DOMAIN } : {}),
});

// ── admin ────────────────────────────────────────────────────────────────────
export function verifyAdminCredentials(user: string, password: string): boolean {
  if (!ADMIN_USER || !ADMIN_PWHASH) return false; // fail closed until env is configured
  const userOk = (user || '').trim().toLowerCase() === ADMIN_USER.toLowerCase();
  const passOk = verifyPassword(password || '', ADMIN_PWHASH);
  return userOk && passOk; // scrypt always runs => uniform timing
}

// Admin tokens are bound to the CURRENT password hash: rotating HIRE_ADMIN_PWHASH (or the
// session secret) instantly invalidates every outstanding admin cookie, which is the only
// revocation lever a single-operator env-configured login has.
const ADMIN_SCOPE = 'hire-admin|' + createHash('sha256').update(ADMIN_PWHASH || 'unset').digest('hex').slice(0, 32);

export function setAdminCookie() {
  const exp = Math.floor(Date.now() / 1000) + ADMIN_MAX_AGE;
  const payload = `hireadm.${exp}`;
  cookies().set(ADMIN_COOKIE, `${payload}.${sign(ADMIN_SCOPE, payload)}`, cookieOpts(ADMIN_MAX_AGE));
}

export function clearAdminCookie() {
  cookies().set(ADMIN_COOKIE, '', { ...cookieOpts(0), maxAge: 0 });
}

export function isHireAdminCookie(): boolean {
  if (!ADMIN_PWHASH) return false; // no operator login configured => no cookie is valid
  return !!parseToken(ADMIN_SCOPE, 'hireadm', cookies().get(ADMIN_COOKIE)?.value, 3);
}

// Admin gate for hire admin APIs: hire-admin cookie OR x-admin-token (MKT_ADMIN_TOKEN).
export async function requireHireAdmin(req: NextRequest): Promise<{ via: 'cookie' | 'token' }> {
  const token = req.headers.get('x-admin-token');
  if (config.adminToken && token && token === config.adminToken) return { via: 'token' };
  if (isHireAdminCookie()) return { via: 'cookie' };
  throw new ApiError(401, 'unauthorized', 'admin login required');
}

// ── customer ─────────────────────────────────────────────────────────────────
export interface HireCustomerCtx {
  id: string;
  email: string;
  name: string;
  company: string | null;
  discord: string | null;
}

export async function issueCustomerSession(customerId: string, epoch: number) {
  const exp = Math.floor(Date.now() / 1000) + CUSTOMER_MAX_AGE;
  const payload = `hirecust.${customerId}.${epoch}.${exp}`;
  cookies().set(CUSTOMER_COOKIE, `${payload}.${sign('hire-customer', payload)}`, cookieOpts(CUSTOMER_MAX_AGE));
}

export function clearCustomerCookie() {
  cookies().set(CUSTOMER_COOKIE, '', { ...cookieOpts(0), maxAge: 0 });
}

export async function currentCustomer(): Promise<HireCustomerCtx | null> {
  const seg = parseToken('hire-customer', 'hirecust', cookies().get(CUSTOMER_COOKIE)?.value, 5);
  if (!seg) return null;
  const [, customerId, epochStr] = seg;
  const cust = await prisma.hireCustomer.findUnique({ where: { id: customerId } });
  if (!cust || cust.sessionEpoch !== Number(epochStr)) return null;
  return { id: cust.id, email: cust.email, name: cust.name, company: cust.company, discord: cust.discord };
}

export async function requireCustomer(): Promise<HireCustomerCtx> {
  const c = await currentCustomer();
  if (!c) throw new ApiError(401, 'unauthorized', 'please sign in');
  return c;
}

// Uniform-timing customer credential check (dummy scrypt when the email is unknown).
export async function verifyCustomerLogin(email: string, password: string) {
  const cust = await prisma.hireCustomer.findUnique({ where: { email: email.toLowerCase() } });
  const ok = cust ? verifyPassword(password, cust.passwordHash) : (verifyPassword(password, DUMMY_HASH), false);
  return ok && cust ? cust : null;
}
