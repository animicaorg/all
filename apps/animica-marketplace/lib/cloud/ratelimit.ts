// Animica Python Cloud — rate limiting and free-tier abuse control (§35, §42).
//
// The existing platform limiter (lib/apikey.ts rateLimit) is an in-process Map: it resets on
// every deploy, is per-instance, and does not cover cookie or anonymous traffic. That is fine
// for a chatty read API and completely inadequate for "anyone on the internet may execute
// arbitrary Python here for free".
//
// So free execution is bounded by a DURABLE counter (UsageCounter rows, the same atomic
// conditional-increment the rest of the platform uses) keyed by a hashed identity, plus a fast
// in-process burst limiter in front of it so a flood never reaches the database at all.
//
// The intent is explicit: free compute must never become a botnet or a mining pool.

import { createHash } from 'node:crypto';
import { prisma } from '../db';
import { ApiError } from '../api';
import { activePolicy } from './pricing';

// ---------------------------------------------------------------------------
// In-process burst limiter (first line of defense)
// ---------------------------------------------------------------------------

interface Bucket {
  count: number;
  resetAt: number;
}
const buckets = new Map<string, Bucket>();
let lastSweep = Date.now();

function sweep(now: number) {
  if (now - lastSweep < 60_000) return;
  lastSweep = now;
  for (const [k, b] of buckets) if (now >= b.resetAt) buckets.delete(k);
}

export function burstLimit(key: string, perMin: number): { ok: boolean; retryAfter: number } {
  const now = Date.now();
  sweep(now);
  const b = buckets.get(key);
  if (!b || now >= b.resetAt) {
    buckets.set(key, { count: 1, resetAt: now + 60_000 });
    return { ok: true, retryAfter: 0 };
  }
  if (b.count >= perMin) return { ok: false, retryAfter: Math.ceil((b.resetAt - now) / 1000) };
  b.count += 1;
  return { ok: true, retryAfter: 0 };
}

// ---------------------------------------------------------------------------
// Caller identity
// ---------------------------------------------------------------------------

/**
 * Derive a stable, privacy-preserving identity for an anonymous caller.
 *
 * We store only a salted hash — never a raw IP.
 *
 * CHOOSING THE HOP MATTERS. A client can send any X-Forwarded-For it likes, and nginx APPENDS
 * the real peer rather than replacing the header. So the LEFTMOST entry is attacker-controlled:
 * keying an abuse counter on it hands out unlimited fresh free-tier identities to anyone who
 * rotates the header, which defeats every free-tier cap at once.
 *
 * The trustworthy values are the ones OUR proxy wrote: `X-Real-IP` (nginx sets it to $remote_addr
 * on every proxied location in animica.dev.conf) and, failing that, the RIGHTMOST X-Forwarded-For
 * entry, which is the hop nearest us. We fall back to a single shared bucket rather than
 * `unknown`-per-request so that a misconfiguration fails CLOSED (everyone shares one small
 * allowance) instead of open (everyone gets their own).
 */
export function anonIdentity(req: Request): string {
  const realIp = (req.headers.get('x-real-ip') ?? '').trim();
  let ip = realIp;
  if (!ip) {
    const parts = (req.headers.get('x-forwarded-for') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean);
    ip = parts.length ? parts[parts.length - 1] : '';
  }
  if (!ip) ip = 'proxy-unresolved';
  const salt = process.env.SESSION_SECRET ?? 'anm-cloud';
  return 'ip:' + createHash('sha256').update(salt + '|' + ip).digest('hex').slice(0, 32);
}

function dayKey(now = new Date()): string {
  return now.toISOString().slice(0, 10);
}
function monthKey(now = new Date()): string {
  return now.toISOString().slice(0, 7);
}

// ---------------------------------------------------------------------------
// Durable free-tier allowance
// ---------------------------------------------------------------------------

export interface FreeTierCheck {
  ok: boolean;
  usedToday: number;
  dailyLimit: number;
  usedMonth: number;
  monthlyLimit: number;
  reason?: string;
}

/**
 * Consume one free execution for an identity (anonymous IP hash, or an account id).
 *
 * Atomic: the conditional updateMany means two concurrent requests for the last free slot
 * cannot both succeed. Both a daily and a monthly ceiling apply — the daily one bounds a burst,
 * the monthly one bounds sustained free consumption and is what makes the free-tier COGS line
 * on /admin/profitability predictable.
 */
export async function consumeFreeExecution(identity: string, now = new Date()): Promise<FreeTierCheck> {
  const policy = await activePolicy();
  const dailyLimit = policy.freeExecutionsPerDay;
  const monthlyLimit = policy.freeExecutionsPerMonth;

  const daily = await consumeCounter(identity, 'cloud_free_day', dayKey(now), dailyLimit);
  if (!daily.ok) {
    return {
      ok: false,
      usedToday: daily.used,
      dailyLimit,
      usedMonth: 0,
      monthlyLimit,
      reason: `Free-tier daily limit reached (${dailyLimit} executions/day). Sign in and add ANM for unmetered access, or try again tomorrow.`,
    };
  }
  const monthly = await consumeCounter(identity, 'cloud_free_month', monthKey(now), monthlyLimit);
  if (!monthly.ok) {
    // Give the daily slot back — the request is being refused, so it must not be charged.
    await releaseCounter(identity, 'cloud_free_day', dayKey(now));
    return {
      ok: false,
      usedToday: daily.used,
      dailyLimit,
      usedMonth: monthly.used,
      monthlyLimit,
      reason: `Free-tier monthly limit reached (${monthlyLimit} executions/month).`,
    };
  }
  return { ok: true, usedToday: daily.used, dailyLimit, usedMonth: monthly.used, monthlyLimit };
}

async function consumeCounter(identity: string, feature: string, period: string, limit: number) {
  await prisma.usageCounter
    .upsert({
      where: { accountId_feature_period: { accountId: identity, feature, period } },
      create: { accountId: identity, feature, period, used: 0 },
      update: {},
    })
    .catch(() => {});
  const res = await prisma.usageCounter.updateMany({
    where: { accountId: identity, feature, period, used: { lte: limit - 1 } },
    data: { used: { increment: 1 } },
  });
  const row = await prisma.usageCounter.findUnique({
    where: { accountId_feature_period: { accountId: identity, feature, period } },
  });
  return { ok: res.count === 1, used: row?.used ?? 0 };
}

async function releaseCounter(identity: string, feature: string, period: string) {
  await prisma.usageCounter
    .updateMany({ where: { accountId: identity, feature, period, used: { gte: 1 } }, data: { used: { decrement: 1 } } })
    .catch(() => {});
}

/** Refund a free slot when the work provably never ran (admission refused downstream). */
export async function refundFreeExecution(identity: string, now = new Date()) {
  await releaseCounter(identity, 'cloud_free_day', dayKey(now));
  await releaseCounter(identity, 'cloud_free_month', monthKey(now));
}

// ---------------------------------------------------------------------------
// Route helper
// ---------------------------------------------------------------------------

export interface LimitOpts {
  /** Requests/minute for the in-process burst check. */
  perMin: number;
  /** Bucket namespace, so deploys and invocations do not share a budget. */
  scope: string;
}

/** Throw a 429 with a Retry-After hint if the caller is over their burst budget. */
export function enforceBurst(identity: string, opts: LimitOpts) {
  const r = burstLimit(`${opts.scope}:${identity}`, opts.perMin);
  if (!r.ok) {
    const e = new ApiError(429, 'rate_limited', `Too many requests. Retry in ${r.retryAfter}s.`);
    e.details = { retryAfter: r.retryAfter, scope: opts.scope };
    throw e;
  }
}

/**
 * The free-tier cost ceiling (§78). Returns true when this UTC month's free-tier COGS has
 * exceeded the configured budget, at which point free executions are refused platform-wide
 * rather than silently running at a loss.
 */
export async function freeTierBudgetExhausted(now = new Date()): Promise<boolean> {
  const policy = await activePolicy();
  if (policy.freeTierMonthlyCeilingNanm <= 0n) return false; // no ceiling configured
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const agg = await prisma.cloudExecution.aggregate({
    where: { freeTier: true, createdAt: { gte: start } },
    _sum: { cogsNanm: true },
  });
  return (agg._sum.cogsNanm ?? 0n) >= policy.freeTierMonthlyCeilingNanm;
}
