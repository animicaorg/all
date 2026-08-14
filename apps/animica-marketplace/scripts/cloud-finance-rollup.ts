import { readFileSync } from 'node:fs';
import { prisma } from '../lib/db';
import { runtime } from '../lib/cloud/config';
import { CURRENT_STATES } from '../lib/planConfig';
import { makeLogger, parseFlags } from './store-worker-util';

// Animica Python Cloud — FinanceDaily rollup (§82). Computes the daily business snapshot for
// one UTC day ENTIRELY from authoritative records:
//
//   ANM economy  <- CloudExecution rows (price/split/COGS/contribution were settled row by row
//                   through lib/ledger.ts; summing them is aggregation, never re-derivation)
//   USD business <- BillingPayment (verified PayPal captures) + PlanSubscription state
//   ANM/USD ref  <- the EXISTING anm-price feed (runtime.anmPriceFile, written by
//                   anm-price.timer) or an AnmPriceSnapshot taken on that day. NEVER invented:
//                   no acceptable source => anmUsdMicros stays 0, which downstream dashboards
//                   must read as "USD-equivalents unavailable" (§79).
//
// FinanceDaily is a CACHE: idempotent upsert keyed on the day, safe to re-run for any past day
// (backfill: `tsx scripts/cloud-finance-rollup.ts --day 2026-08-01`). The daily reconciliation
// worker imports rollupFinanceDay() and runs it before verifying, so there is no separate
// systemd unit for this file.
//
// Money: integer nANM BigInt and integer USD cents throughout. The only float ever touched is
// the market feed's price, converted immediately to integer micro-dollars.

const WORKER = 'animica-cloud-finance';
const log = makeLogger(WORKER);

// Kill switch. Default ON, unlike the executing workers: this job only upserts a cache row
// derived from records that already exist — it moves no money, runs no code, deletes nothing.
const ROLLUP_ENABLED = process.env.CLOUD_FINANCE_ROLLUP_ENABLED !== '0';

// How long after a UTC day closes the live feed still counts as that day's reference. The
// rollup normally runs minutes after midnight, so the feed observed "now" is an honest close
// price for yesterday; beyond this window only a snapshot taken ON the day is acceptable.
const FEED_GRACE_MS = 12 * 3600_000;

export interface DayBounds {
  day: string;
  start: Date;
  end: Date;
}

export function utcDayBounds(day: string): DayBounds | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const start = new Date(`${day}T00:00:00.000Z`);
  // Round-trip check rejects impossible dates like 2026-02-31 that Date would silently shift.
  if (!Number.isFinite(start.getTime()) || start.toISOString().slice(0, 10) !== day) return null;
  return { day, start, end: new Date(start.getTime() + 86_400_000) };
}

export function previousUtcDay(now: Date = new Date()): string {
  return new Date(now.getTime() - 86_400_000).toISOString().slice(0, 10);
}

// ── ANM/USD reference (§79: report a real observation or report nothing) ─────

interface PriceRef {
  usdMicros: bigint;
  source: string;
  observedAt: Date | null;
  snapshotted: boolean;
}

const NO_PRICE: PriceRef = { usdMicros: 0n, source: 'unavailable', observedAt: null, snapshotted: false };

async function priceForDay(bounds: DayBounds, write: boolean): Promise<PriceRef> {
  // 1. A snapshot observed ON the day is the authoritative historical reference — re-running
  //    a past day must reuse it, not today's market.
  const existing = await prisma.anmPriceSnapshot.findFirst({
    where: { observedAt: { gte: bounds.start, lt: bounds.end } },
    orderBy: { observedAt: 'desc' },
  });
  if (existing) {
    return { usdMicros: existing.usdMicros, source: existing.source, observedAt: existing.observedAt, snapshotted: false };
  }

  // 2. The live feed, but only if its own timestamp falls inside the day (or the short grace
  //    window after it). A month-old backfill must NOT get stamped with today's price.
  let feed: { price: number; ts: number; source: string } | null = null;
  try {
    const raw = JSON.parse(readFileSync(runtime.anmPriceFile, 'utf8'));
    const price = Number(raw?.last ?? raw?.mid);
    const ts = Number(raw?.ts);
    if (Number.isFinite(price) && price > 0 && Number.isFinite(ts) && ts > 0) {
      feed = { price, ts, source: typeof raw?.source === 'string' && raw.source ? raw.source : 'nonkyc' };
    }
  } catch {
    feed = null; // missing/corrupt feed => unavailable, never a guess
  }
  if (!feed) return NO_PRICE;

  const observedAt = new Date(feed.ts * 1000);
  if (observedAt < bounds.start || observedAt.getTime() > bounds.end.getTime() + FEED_GRACE_MS) {
    return NO_PRICE; // feed exists but describes some other day => stale for THIS day
  }
  const usdMicros = BigInt(Math.round(feed.price * 1_000_000));
  if (usdMicros <= 0n) return NO_PRICE; // sub-micro price rounds to nothing representable

  if (write) {
    // Idempotent: one snapshot per (source, observation instant).
    const dup = await prisma.anmPriceSnapshot.findFirst({ where: { source: feed.source, observedAt } });
    if (!dup) {
      await prisma.anmPriceSnapshot.create({ data: { usdMicros, source: feed.source, observedAt } });
    }
    return { usdMicros, source: feed.source, observedAt, snapshotted: !dup };
  }
  return { usdMicros, source: feed.source, observedAt, snapshotted: false };
}

// ── The rollup ───────────────────────────────────────────────────────────────

export interface RollupResult {
  day: string;
  written: boolean;
  grossVolumeNanm: bigint;
  platformRevNanm: bigint;
  developerPayNanm: bigint;
  providerPayNanm: bigint;
  cogsNanm: bigint;
  contributionNanm: bigint;
  freeTierCogsNanm: bigint;
  usdRevenueCents: number;
  mrrCents: number;
  newMrrCents: number;
  churnedMrrCents: number;
  executions: number;
  freeExecutions: number;
  activeDevelopers: number;
  payingAccounts: number;
  anmUsdMicros: bigint;
  priceSource: string;
}

/**
 * Compute (and, when `write`, upsert) the FinanceDaily row for one UTC day.
 * Exported so the daily reconcile worker can refresh the cache before verifying it.
 */
export async function rollupFinanceDay(day: string, opts: { write: boolean }): Promise<RollupResult> {
  const bounds = utcDayBounds(day);
  if (!bounds) throw new Error(`invalid UTC day '${day}' (expected YYYY-MM-DD)`);
  const { start, end } = bounds;
  const write = opts.write && ROLLUP_ENABLED;

  const inDay = { gte: start, lt: end };

  const [execAgg, freeAgg, devGroups, paidAgg, subs] = await Promise.all([
    // All executions created in the day. Unsettled rows carry zero money fields, so including
    // them keeps `executions` honest without inflating any sum.
    prisma.cloudExecution.aggregate({
      where: { createdAt: inDay },
      _sum: {
        priceNanm: true,
        platformFeeNanm: true,
        developerNanm: true,
        providerNanm: true,
        cogsNanm: true,
        contributionNanm: true,
      },
      _count: { _all: true },
    }),
    prisma.cloudExecution.aggregate({
      where: { createdAt: inDay, freeTier: true },
      _sum: { cogsNanm: true },
      _count: { _all: true },
    }),
    // "Active developer" = a developer whose functions actually executed that day.
    prisma.cloudExecution.groupBy({ by: ['developerAccountId'], where: { createdAt: inDay } }),
    // Authoritative USD revenue: verified PayPal captures only. REFUNDED/FAILED rows are
    // excluded — revenue that came back is not revenue.
    prisma.billingPayment.aggregate({
      where: { occurredAt: inDay, status: 'COMPLETED' },
      _sum: { amountCents: true },
    }),
    // Every subscription row that could contribute to MRR movement for this day. priceUsdCents
    // is the per-row snapshot of what the subscriber actually pays (§88).
    prisma.planSubscription.findMany({
      where: { createdAt: { lt: end }, priceUsdCents: { gt: 0 } },
      select: {
        accountId: true,
        status: true,
        priceUsdCents: true,
        createdAt: true,
        canceledAt: true,
        lastPaymentAt: true,
      },
    }),
  ]);

  // MRR "as of end of day", reconstructed from the current rows: a sub counts if it is in a
  // current (entitlement-owning) state now, or was canceled AFTER the day closed. This is the
  // best derivation the records support — and because the rollup is a re-runnable cache, any
  // later state change is picked up by simply re-running the day.
  const current = new Set<string>(CURRENT_STATES as readonly string[]);
  let mrrCents = 0;
  let newMrrCents = 0;
  let churnedMrrCents = 0;
  const payers = new Set<string>();
  for (const s of subs) {
    const liveAtEnd = current.has(s.status) || (s.status === 'CANCELED' && s.canceledAt != null && s.canceledAt > end);
    if (liveAtEnd) {
      mrrCents += s.priceUsdCents;
      payers.add(s.accountId);
      if (s.createdAt >= start && (s.lastPaymentAt != null || current.has(s.status))) newMrrCents += s.priceUsdCents;
    }
    if (s.canceledAt != null && s.canceledAt >= start && s.canceledAt < end) churnedMrrCents += s.priceUsdCents;
  }

  const price = await priceForDay(bounds, write);
  if (price.usdMicros === 0n) {
    log('warn', 'anm_price_unavailable', {
      day,
      detail: 'no snapshot on the day and no in-window feed observation — anmUsdMicros recorded as 0 (USD-equivalents unavailable)',
    });
  }

  const result: RollupResult = {
    day,
    written: false,
    grossVolumeNanm: execAgg._sum.priceNanm ?? 0n,
    platformRevNanm: execAgg._sum.platformFeeNanm ?? 0n,
    developerPayNanm: execAgg._sum.developerNanm ?? 0n,
    providerPayNanm: execAgg._sum.providerNanm ?? 0n,
    cogsNanm: execAgg._sum.cogsNanm ?? 0n,
    contributionNanm: execAgg._sum.contributionNanm ?? 0n,
    freeTierCogsNanm: freeAgg._sum.cogsNanm ?? 0n,
    usdRevenueCents: paidAgg._sum.amountCents ?? 0,
    mrrCents,
    newMrrCents,
    churnedMrrCents,
    executions: execAgg._count._all,
    freeExecutions: freeAgg._count._all,
    activeDevelopers: devGroups.length,
    payingAccounts: payers.size,
    anmUsdMicros: price.usdMicros,
    priceSource: price.source,
  };

  if (write) {
    const fields = {
      grossVolumeNanm: result.grossVolumeNanm,
      platformRevNanm: result.platformRevNanm,
      developerPayNanm: result.developerPayNanm,
      providerPayNanm: result.providerPayNanm,
      cogsNanm: result.cogsNanm,
      contributionNanm: result.contributionNanm,
      freeTierCogsNanm: result.freeTierCogsNanm,
      usdRevenueCents: result.usdRevenueCents,
      mrrCents: result.mrrCents,
      newMrrCents: result.newMrrCents,
      churnedMrrCents: result.churnedMrrCents,
      executions: result.executions,
      freeExecutions: result.freeExecutions,
      activeDevelopers: result.activeDevelopers,
      payingAccounts: result.payingAccounts,
      anmUsdMicros: result.anmUsdMicros,
      computedAt: new Date(),
    };
    await prisma.financeDaily.upsert({ where: { day }, create: { day, ...fields }, update: fields });
    result.written = true;
  }
  return result;
}

// ── Standalone entry point (backfills) ───────────────────────────────────────
// Only runs when this file is the invoked script — importing rollupFinanceDay() from the
// reconcile worker must not trigger a run.

function argValue(name: string): string | null {
  for (const a of process.argv.slice(2)) {
    if (a.startsWith(`${name}=`)) return a.slice(name.length + 1);
  }
  return null;
}

const invokedDirectly = Boolean(process.argv[1] && /cloud-finance-rollup\.(ts|js|mjs|cjs)$/.test(process.argv[1]));

if (invokedDirectly) {
  (async () => {
    const { dryRun } = parseFlags();
    const day = argValue('--day') ?? previousUtcDay();
    if (!ROLLUP_ENABLED) log('info', 'rollup_disabled', { detail: 'CLOUD_FINANCE_ROLLUP_ENABLED=0 — computing without writing' });
    const r = await rollupFinanceDay(day, { write: !dryRun });
    log('info', r.written ? 'rollup_written' : 'rollup_computed_only', { dryRun, ...r });
  })()
    .catch((e) => {
      log('error', 'run_crashed', { error: String(e?.stack ?? e) });
      process.exitCode = 1;
    })
    .finally(async () => {
      await prisma.$disconnect();
    });
}
