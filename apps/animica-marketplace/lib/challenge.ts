import { createHmac, timingSafeEqual } from 'node:crypto';
import { config } from './config';
import { prisma } from './db';

// Stateless wallet-login challenge: no DB/Redis needed. The challenge embeds the claimed address +
// a timestamp + an HMAC. At verify time we re-derive the HMAC and check freshness. The wallet signs
// the WHOLE challenge string via animica_signMessage; the server then ml_dsa_65-verifies it.
//
// v2 (store/dev-portal): the shipped extension still blind-signs, so v2 challenges assume a
// hostile dapp can obtain arbitrary-string signatures UNLESS the string is single-purpose.
// v2 binds purpose + audience into the signed string, keeps the recognizable `animica-login|`
// prefix (the extension's SignApprove.tsx sensitive-prefix warning matches it), and is
// SINGLE-USE via a ChallengeBurn row keyed on the MAC — a signed challenge can never be
// replayed into a second session/consent.

const TTL_MS = 5 * 60 * 1000;
const SKEW_MS = 2 * 60 * 1000; // reject timestamps from the future beyond small clock skew

export function issueChallenge(address: string): string {
  const ts = Date.now();
  const payload = `animica-login|${address}|${ts}`;
  const mac = createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
  return `${payload}|${mac}`;
}

export function validateChallenge(challenge: string, address: string): boolean {
  const parts = challenge.split('|');
  if (parts.length !== 4) return false;
  const [tag, addr, ts, mac] = parts;
  if (tag !== 'animica-login' || addr !== address) return false;
  if (Date.now() - Number(ts) > TTL_MS) return false;
  const expected = createHmac('sha256', config.sessionSecret).update(`${tag}|${addr}|${ts}`).digest('base64url');
  try {
    return timingSafeEqual(Buffer.from(mac), Buffer.from(expected));
  } catch {
    return false;
  }
}

// ── v2: purpose-bound, audience-bound, single-use ────────────────────────────
// Format: animica-login|v2|purpose=<p>|aud=<host>[|<k>=<v>...]|<address>|<ts>|<mac>
// Extra k=v segments carry consent parameters (e.g. subscribe: listing/period/amount) INSIDE
// the signed string, so what the user approved is exactly what the server enforces.

export const CHALLENGE_PURPOSES = ['devportal', 'store', 'subscribe', 'review'] as const;
export type ChallengePurpose = (typeof CHALLENGE_PURPOSES)[number];

// Audience = our public host. A signature minted for animica.dev can never be replayed
// against another deployment of this codebase (and vice versa).
export const CHALLENGE_AUDIENCE = (() => {
  try {
    return new URL(config.baseUrl).host;
  } catch {
    return 'animica.dev';
  }
})();

const MAX_CHALLENGE_LEN = 1024;
const PARAM_KEY_RE = /^[a-z][a-z0-9_]{0,31}$/;
const PARAM_VALUE_RE = /^[A-Za-z0-9._:-]{1,128}$/;
const RESERVED_PARAM_KEYS = new Set(['purpose', 'aud']);

function hmac(payload: string): string {
  return createHmac('sha256', config.sessionSecret).update(payload).digest('base64url');
}

export interface ChallengeV2 {
  purpose: ChallengePurpose;
  aud: string;
  address: string;
  ts: number;
  mac: string;
  params: Record<string, string>; // extra bound key=value segments (may be empty)
}

export type ChallengeV2Result = { ok: true; challenge: ChallengeV2 } | { ok: false; reason: string };

// Issue a v2 challenge. `extra` entries are bound into the signed string in insertion order.
// Throws on invalid input (server-side bug, not a runtime condition).
export function issueChallengeV2(
  address: string,
  purpose: ChallengePurpose,
  extra: Record<string, string> = {},
): string {
  if (!CHALLENGE_PURPOSES.includes(purpose)) throw new Error(`bad challenge purpose: ${purpose}`);
  const segs = [`animica-login`, 'v2', `purpose=${purpose}`, `aud=${CHALLENGE_AUDIENCE}`];
  for (const [k, v] of Object.entries(extra)) {
    if (!PARAM_KEY_RE.test(k) || RESERVED_PARAM_KEYS.has(k)) throw new Error(`bad challenge param key: ${k}`);
    if (!PARAM_VALUE_RE.test(v)) throw new Error(`bad challenge param value for ${k}`);
    segs.push(`${k}=${v}`);
  }
  segs.push(address, String(Date.now()));
  const payload = segs.join('|');
  const out = `${payload}|${hmac(payload)}`;
  if (out.length > MAX_CHALLENGE_LEN) throw new Error('challenge too long');
  return out;
}

// Strict, fail-closed v2 validation (pure — no DB). Rejects on ANY anomaly: wrong tag/version,
// purpose/audience/address mismatch, malformed or duplicate params, stale or future timestamp,
// bad MAC. Does NOT burn — use consumeChallengeV2 for single-use enforcement.
export function validateChallengeV2(
  challenge: string,
  address: string,
  purpose: ChallengePurpose,
): ChallengeV2Result {
  if (typeof challenge !== 'string' || challenge.length > MAX_CHALLENGE_LEN) return { ok: false, reason: 'bad challenge' };
  const parts = challenge.split('|');
  if (parts.length < 7) return { ok: false, reason: 'too few segments' };
  if (parts[0] !== 'animica-login' || parts[1] !== 'v2') return { ok: false, reason: 'bad tag/version' };
  if (parts[2] !== `purpose=${purpose}` || !CHALLENGE_PURPOSES.includes(purpose)) {
    return { ok: false, reason: 'purpose mismatch' };
  }
  if (parts[3] !== `aud=${CHALLENGE_AUDIENCE}`) return { ok: false, reason: 'audience mismatch' };

  const mac = parts[parts.length - 1];
  const tsStr = parts[parts.length - 2];
  const addr = parts[parts.length - 3];
  if (addr !== address) return { ok: false, reason: 'address mismatch' };
  if (!/^[0-9]{1,16}$/.test(tsStr)) return { ok: false, reason: 'bad timestamp' };
  const ts = Number(tsStr);
  if (Date.now() - ts > TTL_MS) return { ok: false, reason: 'expired' };
  if (ts - Date.now() > SKEW_MS) return { ok: false, reason: 'timestamp in the future' };

  const params: Record<string, string> = {};
  for (const seg of parts.slice(4, parts.length - 3)) {
    const eq = seg.indexOf('=');
    if (eq <= 0) return { ok: false, reason: 'malformed param' };
    const k = seg.slice(0, eq);
    const v = seg.slice(eq + 1);
    if (!PARAM_KEY_RE.test(k) || RESERVED_PARAM_KEYS.has(k) || k in params) return { ok: false, reason: 'bad param key' };
    if (!PARAM_VALUE_RE.test(v)) return { ok: false, reason: 'bad param value' };
    params[k] = v;
  }

  const payload = parts.slice(0, -1).join('|');
  const expected = hmac(payload);
  try {
    if (!timingSafeEqual(Buffer.from(mac), Buffer.from(expected))) return { ok: false, reason: 'bad mac' };
  } catch {
    return { ok: false, reason: 'bad mac' };
  }
  return { ok: true, challenge: { purpose, aud: CHALLENGE_AUDIENCE, address: addr, ts, mac, params } };
}

// Validate AND burn: the only correct entry point for verify/consent routes. The unique-mac
// insert is the atomic single-use gate — a concurrent replay loses the race and fails. Any
// other DB failure propagates (fail-closed: no burn record => no session/consent).
export async function consumeChallengeV2(
  challenge: string,
  address: string,
  purpose: ChallengePurpose,
): Promise<ChallengeV2Result> {
  const v = validateChallengeV2(challenge, address, purpose);
  if (!v.ok) return v;
  try {
    await prisma.challengeBurn.create({ data: { mac: v.challenge.mac } });
  } catch (e: any) {
    if (e?.code === 'P2002') return { ok: false, reason: 'challenge already used' };
    throw e;
  }
  return v;
}

// Sweep burn rows older than the challenge TTL (plus margin) — safe to prune because the
// challenge itself has long expired. Called from a maintenance worker.
export async function sweepChallengeBurns(olderThanMs = 24 * 60 * 60 * 1000): Promise<number> {
  const r = await prisma.challengeBurn.deleteMany({
    where: { usedAt: { lt: new Date(Date.now() - Math.max(olderThanMs, TTL_MS + SKEW_MS)) } },
  });
  return r.count;
}
