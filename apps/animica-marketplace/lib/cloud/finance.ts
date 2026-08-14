// Animica Python Cloud — the financial reporting engine (§74-§84, §90, §92-§95).
//
// Answers, from AUTHORITATIVE ROWS ONLY, the operator's questions: how much did Animica make,
// what did it cost, which product made it, what is the margin, and which workloads lose money.
//
// Sources of truth (and nothing else):
//   CloudExecution     — every priced/free execution with its settled money + COGS fields
//   CloudAppPurchase   — every marketplace sale with its settled split
//   BillingPayment     — every VERIFIED USD capture (PayPal)
//   PlanSubscription   — subscription history (price snapshots per row)
//   LedgerEntry        — used by the reconcile worker; here only for drill-downs
//   AnmPriceSnapshot / the anm-price feed file — the ONLY acceptable ANM/USD references (§79)
//
// Explicitly NOT used: Account.balanceNanm, CloudApp.revenueNanm, CloudFunction.execCount,
// CloudProvider.earnedNanm, FinanceDaily — those are caches; this module recomputes.
//
// §80: gross transaction volume is NEVER labelled revenue. The fields are named apart
// (grossVolumeNanm vs platformRevenueNanm) and every consumer must keep them apart.
// §93: no LTV/CAC anywhere — there is no real acquisition-cost data to compute them from.
// §79: when no acceptable ANM/USD reference exists, ANM figures are reported WITHOUT a USD
// equivalent (anmUsdRef() returns null; nothing is invented).
//
// Money: integer nANM bigint + integer USD cents. Ratios in basis points (integer math).

import { readFileSync } from 'node:fs';
import { prisma } from '../db';
import { runtime } from './config';

// ---------------------------------------------------------------------------
// Ranges
// ---------------------------------------------------------------------------

export const RANGE_KEYS = ['today', '24h', '7d', '30d', 'mtd', '90d', 'all'] as const;
export type RangeKey = (typeof RANGE_KEYS)[number];

export interface Range {
  key: RangeKey;
  /** null => unbounded (all history). */
  start: Date | null;
  end: Date;
  label: string;
}

export function isRangeKey(v: unknown): v is RangeKey {
  return typeof v === 'string' && (RANGE_KEYS as readonly string[]).includes(v);
}

export function rangeFor(key: RangeKey, now: Date = new Date()): Range {
  const end = now;
  switch (key) {
    case 'today': {
      const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
      return { key, start, end, label: 'Today (UTC)' };
    }
    case '24h':
      return { key, start: new Date(now.getTime() - 86_400_000), end, label: 'Last 24 hours' };
    case '7d':
      return { key, start: new Date(now.getTime() - 7 * 86_400_000), end, label: 'Last 7 days' };
    case '30d':
      return { key, start: new Date(now.getTime() - 30 * 86_400_000), end, label: 'Last 30 days' };
    case 'mtd': {
      const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
      return { key, start, end, label: 'Month to date (UTC)' };
    }
    case '90d':
      return { key, start: new Date(now.getTime() - 90 * 86_400_000), end, label: 'Last 90 days' };
    case 'all':
      return { key, start: null, end, label: 'All time' };
  }
}

/** Prisma `createdAt` filter for a range (empty object for all-time). */
function within(range: Range): { gte?: Date; lt: Date } {
  return range.start ? { gte: range.start, lt: range.end } : { lt: range.end };
}

function marginBpsOf(profit: bigint, revenue: bigint): number | null {
  if (revenue <= 0n) return null;
  return Number((profit * 10_000n) / revenue);
}

// ---------------------------------------------------------------------------
// ANM/USD reference (§79 — a real observation or nothing)
// ---------------------------------------------------------------------------

export interface AnmUsdRef {
  usdMicros: bigint; // USD price of 1 ANM in micro-dollars
  source: string;
  observedAt: Date;
}

/** Live feed observations older than this are stale — report ANM-only instead. */
const PRICE_MAX_AGE_MS = 48 * 3600_000;

/**
 * The current ANM/USD reference: the freshest AnmPriceSnapshot, falling back to the live
 * anm-price feed file (written by anm-price.timer). Returns null when neither has a fresh
 * observation — callers must then omit USD equivalents entirely, never guess.
 */
export async function anmUsdRef(now: Date = new Date()): Promise<AnmUsdRef | null> {
  const cutoff = new Date(now.getTime() - PRICE_MAX_AGE_MS);
  const snap = await prisma.anmPriceSnapshot
    .findFirst({ where: { observedAt: { gte: cutoff } }, orderBy: { observedAt: 'desc' } })
    .catch(() => null);
  if (snap && snap.usdMicros > 0n) {
    return { usdMicros: snap.usdMicros, source: snap.source, observedAt: snap.observedAt };
  }
  try {
    const raw = JSON.parse(readFileSync(runtime.anmPriceFile, 'utf8'));
    const price = Number(raw?.last ?? raw?.mid);
    const ts = Number(raw?.ts);
    if (Number.isFinite(price) && price > 0 && Number.isFinite(ts) && ts * 1000 >= cutoff.getTime()) {
      const usdMicros = BigInt(Math.round(price * 1_000_000));
      if (usdMicros > 0n) {
        return {
          usdMicros,
          source: typeof raw?.source === 'string' && raw.source ? raw.source : 'nonkyc',
          observedAt: new Date(ts * 1000),
        };
      }
    }
  } catch {
    /* missing/corrupt feed => no reference */
  }
  return null;
}

/** nANM -> whole USD cents at a reference price. Floor; display-only, never money movement. */
export function nanmToUsdCents(nanm: bigint, usdMicros: bigint): number {
  // nanm/1e9 ANM * usdMicros/1e6 USD/ANM * 100 cents/USD = nanm*usdMicros/1e13
  return Number((nanm * usdMicros) / 10_000_000_000_000n);
}

/** nANM -> micro-dollars (for sums far below one cent, which ANM amounts usually are). */
export function nanmToUsdMicros(nanm: bigint, usdMicros: bigint): bigint {
  return (nanm * usdMicros) / 1_000_000_000n;
}

// ---------------------------------------------------------------------------
// 1. Revenue (§80: platform revenue and gross volume are DIFFERENT numbers)
// ---------------------------------------------------------------------------

export interface RevenueReport {
  /** Animica's revenue: execution platform fees + marketplace platform fees. */
  platformRevenueNanm: bigint;
  /** Total customer spend passing through the platform. NOT Animica revenue (§80). */
  grossVolumeNanm: bigint;
  /** Platform fee earned on executions. */
  executionRevenueNanm: bigint;
  /** Platform fee earned on app purchases. */
  marketplaceRevenueNanm: bigint;
  /** Platform fee earned on AI-consuming executions (a subset of executionRevenueNanm). */
  aiRevenueNanm: bigint;
  developerPayoutsNanm: bigint; // execution + purchase developer shares
  providerPayoutsNanm: bigint;
  executionGrossNanm: bigint;
  purchaseGrossNanm: bigint;
  executions: number;
  pricedExecutions: number;
  aiExecutions: number;
  purchases: number;
}

export async function revenueFor(range: Range): Promise<RevenueReport> {
  const inRange = within(range);
  const [execAgg, pricedCount, aiAgg, purchaseAgg] = await Promise.all([
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange },
      _sum: { priceNanm: true, platformFeeNanm: true, developerNanm: true, providerNanm: true },
      _count: { _all: true },
    }),
    prisma.cloudExecution.count({ where: { createdAt: inRange, priceNanm: { gt: 0n } } }),
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange, OR: [{ aiCalls: { gt: 0 } }, { aiTokensIn: { gt: 0 } }, { aiTokensOut: { gt: 0 } }] },
      _sum: { platformFeeNanm: true },
      _count: { _all: true },
    }),
    prisma.cloudAppPurchase.aggregate({
      where: { createdAt: inRange, status: { not: 'REFUNDED' } },
      _sum: { amountNanm: true, platformFeeNanm: true, developerNanm: true },
      _count: { _all: true },
    }),
  ]);

  const executionRevenueNanm = execAgg._sum.platformFeeNanm ?? 0n;
  const marketplaceRevenueNanm = purchaseAgg._sum.platformFeeNanm ?? 0n;
  const executionGrossNanm = execAgg._sum.priceNanm ?? 0n;
  const purchaseGrossNanm = purchaseAgg._sum.amountNanm ?? 0n;

  return {
    platformRevenueNanm: executionRevenueNanm + marketplaceRevenueNanm,
    grossVolumeNanm: executionGrossNanm + purchaseGrossNanm,
    executionRevenueNanm,
    marketplaceRevenueNanm,
    aiRevenueNanm: aiAgg._sum.platformFeeNanm ?? 0n,
    developerPayoutsNanm: (execAgg._sum.developerNanm ?? 0n) + (purchaseAgg._sum.developerNanm ?? 0n),
    providerPayoutsNanm: execAgg._sum.providerNanm ?? 0n,
    executionGrossNanm,
    purchaseGrossNanm,
    executions: execAgg._count._all,
    pricedExecutions: pricedCount,
    aiExecutions: aiAgg._count._all,
    purchases: purchaseAgg._count._all,
  };
}

// ---------------------------------------------------------------------------
// 2. COGS (§74) — internal cost, never customer-visible
// ---------------------------------------------------------------------------

export interface CogsReport {
  computeNanm: bigint;
  aiNanm: bigint;
  infraNanm: bigint;
  /** Platform-absorbed cost of credit-funded execution (promo credits, §75). */
  promoNanm: bigint;
  totalNanm: bigint;
  /** COGS of free-tier executions — the real cost of the free tier (§78). */
  freeTierNanm: bigint;
  freeTierExecutions: number;
}

export async function cogsFor(range: Range): Promise<CogsReport> {
  const inRange = within(range);
  const [agg, freeAgg] = await Promise.all([
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange },
      _sum: { cogsNanm: true, cogsAiNanm: true, cogsComputeNanm: true, cogsInfraNanm: true, cogsPromoNanm: true },
    }),
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange, freeTier: true },
      _sum: { cogsNanm: true },
      _count: { _all: true },
    }),
  ]);
  return {
    computeNanm: agg._sum.cogsComputeNanm ?? 0n,
    aiNanm: agg._sum.cogsAiNanm ?? 0n,
    infraNanm: agg._sum.cogsInfraNanm ?? 0n,
    promoNanm: agg._sum.cogsPromoNanm ?? 0n,
    totalNanm: agg._sum.cogsNanm ?? 0n,
    freeTierNanm: freeAgg._sum.cogsNanm ?? 0n,
    freeTierExecutions: freeAgg._count._all,
  };
}

// ---------------------------------------------------------------------------
// 3. Profit (§82)
// ---------------------------------------------------------------------------

export interface ProfitReport {
  revenueNanm: bigint; // platform revenue (fees), NOT gross volume
  cogsNanm: bigint; // all execution COGS incl. free-tier + promo
  grossProfitNanm: bigint; // revenue - all COGS
  grossMarginBps: number | null;
  /**
   * Contribution of revenue-generating work only: SUM(contributionNanm) over non-free
   * executions + marketplace fees (which have no direct COGS). Excludes free-tier COGS,
   * which is acquisition spend, not a cost of revenue-generating executions.
   */
  contributionNanm: bigint;
  contributionMarginBps: number | null;
  negativeMarginExecutions: number; // priced executions where contribution < 0 (§90)
}

export async function profitFor(range: Range): Promise<ProfitReport> {
  const inRange = within(range);
  const [rev, cogs, paidContrib, losing] = await Promise.all([
    revenueFor(range),
    cogsFor(range),
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange, freeTier: false },
      _sum: { contributionNanm: true },
    }),
    prisma.cloudExecution.count({
      where: { createdAt: inRange, priceNanm: { gt: 0n }, contributionNanm: { lt: 0n } },
    }),
  ]);
  const revenueNanm = rev.platformRevenueNanm;
  const grossProfitNanm = revenueNanm - cogs.totalNanm;
  const contributionNanm = (paidContrib._sum.contributionNanm ?? 0n) + rev.marketplaceRevenueNanm;
  return {
    revenueNanm,
    cogsNanm: cogs.totalNanm,
    grossProfitNanm,
    grossMarginBps: marginBpsOf(grossProfitNanm, revenueNanm),
    contributionNanm,
    contributionMarginBps: marginBpsOf(contributionNanm, revenueNanm),
    negativeMarginExecutions: losing,
  };
}

// ---------------------------------------------------------------------------
// 4. Unit economics (§83)
// ---------------------------------------------------------------------------

export interface UnitEconomicsReport {
  executions: number;
  pricedExecutions: number;
  /** Animica revenue per priced execution. */
  revenuePerExecutionNanm: bigint;
  /** COGS per execution (all executions — free ones burn real compute too). */
  costPerExecutionNanm: bigint;
  /** Gross profit per priced execution. */
  profitPerExecutionNanm: bigint;
  /** Average customer price per priced execution. */
  avgPricePerExecutionNanm: bigint;
  payingCallers: number;
  revenuePerPayingCallerNanm: bigint;
  developersEarning: number;
  avgDeveloperRevenueNanm: bigint; // developer payouts / developers who earned
  /** Animica's realized take rate: platform revenue / gross volume, in bps. */
  takeRateBps: number | null;
}

export async function unitEconomics(range: Range): Promise<UnitEconomicsReport> {
  const inRange = within(range);
  const [rev, cogs, payers, earners] = await Promise.all([
    revenueFor(range),
    cogsFor(range),
    prisma.cloudExecution.groupBy({
      by: ['callerAccountId'],
      where: { createdAt: inRange, priceNanm: { gt: 0n }, callerAccountId: { not: null } },
    }),
    prisma.cloudExecution.groupBy({
      by: ['developerAccountId'],
      where: { createdAt: inRange, developerNanm: { gt: 0n } },
      _sum: { developerNanm: true },
    }),
  ]);
  const priced = BigInt(Math.max(1, rev.pricedExecutions));
  const all = BigInt(Math.max(1, rev.executions));
  const devPaidTotal = earners.reduce((a, r) => a + (r._sum.developerNanm ?? 0n), 0n);
  return {
    executions: rev.executions,
    pricedExecutions: rev.pricedExecutions,
    revenuePerExecutionNanm: rev.pricedExecutions ? rev.executionRevenueNanm / priced : 0n,
    costPerExecutionNanm: rev.executions ? cogs.totalNanm / all : 0n,
    profitPerExecutionNanm: rev.pricedExecutions ? (rev.executionRevenueNanm - cogs.totalNanm) / priced : 0n,
    avgPricePerExecutionNanm: rev.pricedExecutions ? rev.executionGrossNanm / priced : 0n,
    payingCallers: payers.length,
    revenuePerPayingCallerNanm: payers.length ? rev.platformRevenueNanm / BigInt(payers.length) : 0n,
    developersEarning: earners.length,
    avgDeveloperRevenueNanm: earners.length ? devPaidTotal / BigInt(earners.length) : 0n,
    takeRateBps: rev.grossVolumeNanm > 0n ? Number((rev.platformRevenueNanm * 10_000n) / rev.grossVolumeNanm) : null,
  };
}

// ---------------------------------------------------------------------------
// 5. USD metrics (§81) — from real BillingPayment + PlanSubscription history
// ---------------------------------------------------------------------------

/** MRR-recognizing states: paid limits still granted, revenue still expected. SUSPENDED is
 *  on free limits and is deliberately NOT counted (the UI states this basis). */
const MRR_STATES = ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD'] as const;

export interface UsdMetricsReport {
  collectedCents: number; // verified captures in range (COMPLETED)
  refundedCents: number;
  paymentsCount: number;
  mrrCents: number; // as of range.end
  arrCents: number;
  paidSubscribers: number;
  totalAccounts: number;
  arpuCents: number; // MRR / all accounts
  arppuCents: number; // MRR / paying subscribers
  newMrrCents: number;
  expansionMrrCents: number;
  contractionMrrCents: number;
  churnedMrrCents: number;
  mrrBasis: string; // human explanation of what MRR counts
}

export async function usdMetrics(range: Range): Promise<UsdMetricsReport> {
  const inRange = within(range);
  const [collected, refunded, totalAccounts, subs] = await Promise.all([
    prisma.billingPayment.aggregate({
      where: { occurredAt: inRange, status: 'COMPLETED' },
      _sum: { amountCents: true },
      _count: { _all: true },
    }),
    prisma.billingPayment.aggregate({
      where: { occurredAt: inRange, status: 'REFUNDED' },
      _sum: { amountCents: true },
    }),
    prisma.account.count({ where: { createdAt: { lt: range.end } } }),
    // Full paid-subscription history: rows are price snapshots (§88), so MRR movements can be
    // reconstructed from rows alone. PENDING drafts never granted entitlements — excluded.
    prisma.planSubscription.findMany({
      where: { priceUsdCents: { gt: 0 }, status: { not: 'PENDING' }, createdAt: { lt: range.end } },
      select: { id: true, accountId: true, status: true, priceUsdCents: true, createdAt: true, canceledAt: true },
      orderBy: { createdAt: 'asc' },
    }),
  ]);

  const liveAtEnd = (s: (typeof subs)[number]) =>
    (MRR_STATES as readonly string[]).includes(s.status) || (s.status === 'CANCELED' && s.canceledAt != null && s.canceledAt > range.end);

  let mrrCents = 0;
  const payers = new Set<string>();
  for (const s of subs) {
    if (liveAtEnd(s)) {
      mrrCents += s.priceUsdCents;
      payers.add(s.accountId);
    }
  }

  // MRR movements inside the range, per account, from row history.
  const byAccount = new Map<string, typeof subs>();
  for (const s of subs) {
    const arr = byAccount.get(s.accountId) ?? [];
    arr.push(s);
    byAccount.set(s.accountId, arr);
  }
  const start = range.start ?? new Date(0);
  let newMrrCents = 0;
  let expansionMrrCents = 0;
  let contractionMrrCents = 0;
  let churnedMrrCents = 0;

  for (const [, rows] of byAccount) {
    const accountLiveNow = rows.some(liveAtEnd);
    for (let i = 0; i < rows.length; i++) {
      const s = rows[i];
      // Creation events in range: new vs expansion vs contraction (vs prior row's price).
      if (s.createdAt >= start && s.createdAt < range.end) {
        const prev = i > 0 ? rows[i - 1] : null;
        if (!prev) newMrrCents += s.priceUsdCents;
        else if (s.priceUsdCents > prev.priceUsdCents) expansionMrrCents += s.priceUsdCents - prev.priceUsdCents;
        else if (s.priceUsdCents < prev.priceUsdCents) contractionMrrCents += prev.priceUsdCents - s.priceUsdCents;
      }
      // Cancellations in range with no live replacement: churn.
      if (s.canceledAt != null && s.canceledAt >= start && s.canceledAt < range.end && !accountLiveNow) {
        // Only the account's LAST canceled row counts (intermediate rows were upgrades).
        if (i === rows.length - 1) churnedMrrCents += s.priceUsdCents;
      }
    }
  }

  return {
    collectedCents: collected._sum.amountCents ?? 0,
    refundedCents: refunded._sum.amountCents ?? 0,
    paymentsCount: collected._count._all,
    mrrCents,
    arrCents: mrrCents * 12,
    paidSubscribers: payers.size,
    totalAccounts,
    arpuCents: totalAccounts ? Math.round(mrrCents / totalAccounts) : 0,
    arppuCents: payers.size ? Math.round(mrrCents / payers.size) : 0,
    newMrrCents,
    expansionMrrCents,
    contractionMrrCents,
    churnedMrrCents,
    mrrBasis: `sum of live subscription price snapshots in ${MRR_STATES.join('/')} as of range end; SUSPENDED excluded (free limits)`,
  };
}

// ---------------------------------------------------------------------------
// 6. Product breakdown (§84)
// ---------------------------------------------------------------------------

export interface ProductLine {
  key: string;
  name: string;
  currency: 'ANM' | 'USD';
  note: string;
  /** True when this line overlaps others (AI spans Functions/Apps/Agents) — not summable. */
  overlaps: boolean;
  revenueNanm: bigint; // Animica platform fees for the line (ANM lines)
  grossNanm: bigint;
  cogsNanm: bigint;
  profitNanm: bigint;
  payoutsNanm: bigint; // developer + provider payouts inside the line
  revenueCents: number; // USD lines
  marginBps: number | null; // null => not computable from the data (never a fake 0)
  users: number;
  developers: number;
  executions: number;
}

async function executionLine(
  key: string,
  name: string,
  note: string,
  where: Record<string, unknown>,
  range: Range,
  overlaps = false,
  cogsField: 'cogsNanm' | 'ai' = 'cogsNanm',
): Promise<ProductLine> {
  const fullWhere = { createdAt: within(range), ...where };
  const [agg, callers, devs] = await Promise.all([
    prisma.cloudExecution.aggregate({
      where: fullWhere as any,
      _sum: {
        priceNanm: true,
        platformFeeNanm: true,
        developerNanm: true,
        providerNanm: true,
        cogsNanm: true,
        cogsAiNanm: true,
      },
      _count: { _all: true },
    }),
    prisma.cloudExecution.groupBy({ by: ['callerAccountId'], where: fullWhere as any }),
    prisma.cloudExecution.groupBy({ by: ['developerAccountId'], where: fullWhere as any }),
  ]);
  const revenueNanm = agg._sum.platformFeeNanm ?? 0n;
  const cogsNanm = cogsField === 'ai' ? agg._sum.cogsAiNanm ?? 0n : agg._sum.cogsNanm ?? 0n;
  const profitNanm = revenueNanm - cogsNanm;
  return {
    key,
    name,
    currency: 'ANM',
    note,
    overlaps,
    revenueNanm,
    grossNanm: agg._sum.priceNanm ?? 0n,
    cogsNanm,
    profitNanm,
    payoutsNanm: (agg._sum.developerNanm ?? 0n) + (agg._sum.providerNanm ?? 0n),
    revenueCents: 0,
    marginBps: marginBpsOf(profitNanm, revenueNanm),
    users: callers.filter((c) => c.callerAccountId != null).length,
    developers: devs.length,
    executions: agg._count._all,
  };
}

export async function productBreakdown(range: Range): Promise<ProductLine[]> {
  const inRange = within(range);
  const [functions, apps, agents, ai, compute, purchaseAgg, purchaseBuyers, purchaseApps, subPay, subPayers, entPay, entPayers] =
    await Promise.all([
      executionLine('functions', 'Functions', 'Direct function invocations (no app, no agent)', { appId: null, agentId: null }, range),
      executionLine('apps', 'Apps', 'Executions attributed to a published app', { appId: { not: null }, agentId: null }, range),
      executionLine('agents', 'Agents', 'Executions run by autonomous agents', { agentId: { not: null } }, range),
      executionLine(
        'ai',
        'AI inference',
        'AI-consuming executions — platform fee vs AI COGS only. Overlaps the lines above; do not sum.',
        { OR: [{ aiCalls: { gt: 0 } }, { aiTokensIn: { gt: 0 } }, { aiTokensOut: { gt: 0 } }] },
        range,
        true,
        'ai',
      ),
      executionLine(
        'compute',
        'Compute (fleet)',
        'Executions dispatched to community compute providers. Overlaps the product lines above; do not sum.',
        { lane: 'fleet' },
        range,
        true,
      ),
      prisma.cloudAppPurchase.aggregate({
        where: { createdAt: inRange, status: { not: 'REFUNDED' } },
        _sum: { amountNanm: true, platformFeeNanm: true, developerNanm: true },
        _count: { _all: true },
      }),
      prisma.cloudAppPurchase.groupBy({ by: ['accountId'], where: { createdAt: inRange, status: { not: 'REFUNDED' } } }),
      prisma.cloudAppPurchase.groupBy({ by: ['appId'], where: { createdAt: inRange, status: { not: 'REFUNDED' } } }),
      prisma.billingPayment.aggregate({
        where: { occurredAt: inRange, status: 'COMPLETED', kind: 'subscription' },
        _sum: { amountCents: true },
        _count: { _all: true },
      }),
      prisma.billingPayment.groupBy({
        by: ['accountId'],
        where: { occurredAt: inRange, status: 'COMPLETED', kind: 'subscription' },
      }),
      prisma.billingPayment.aggregate({
        where: { occurredAt: inRange, status: 'COMPLETED', kind: { in: ['enterprise', 'service'] } },
        _sum: { amountCents: true },
        _count: { _all: true },
      }),
      prisma.billingPayment.groupBy({
        by: ['accountId'],
        where: { occurredAt: inRange, status: 'COMPLETED', kind: { in: ['enterprise', 'service'] } },
      }),
    ]);

  const mktRevenue = purchaseAgg._sum.platformFeeNanm ?? 0n;
  const marketplace: ProductLine = {
    key: 'marketplace',
    name: 'Marketplace',
    currency: 'ANM',
    note: 'App purchases (one-time + ANM subscriptions). No direct COGS is metered on a sale.',
    overlaps: false,
    revenueNanm: mktRevenue,
    grossNanm: purchaseAgg._sum.amountNanm ?? 0n,
    cogsNanm: 0n,
    profitNanm: mktRevenue,
    payoutsNanm: purchaseAgg._sum.developerNanm ?? 0n,
    revenueCents: 0,
    marginBps: mktRevenue > 0n ? 10_000 : null,
    users: purchaseBuyers.length,
    developers: purchaseApps.length, // distinct apps sold; developer resolution is a drill-down
    executions: purchaseAgg._count._all,
  };

  const subscriptions: ProductLine = {
    key: 'subscriptions',
    name: 'Subscriptions (USD)',
    currency: 'USD',
    note: 'Verified PayPal captures for plan subscriptions. COGS is not allocated per plan — margin not available.',
    overlaps: false,
    revenueNanm: 0n,
    grossNanm: 0n,
    cogsNanm: 0n,
    profitNanm: 0n,
    payoutsNanm: 0n,
    revenueCents: subPay._sum.amountCents ?? 0,
    marginBps: null,
    users: subPayers.length,
    developers: 0,
    executions: subPay._count._all, // payments count for USD lines
  };

  const enterprise: ProductLine = {
    key: 'enterprise',
    name: 'Enterprise & services (USD)',
    currency: 'USD',
    note: 'Verified USD captures with kind enterprise/service. COGS not allocated — margin not available.',
    overlaps: false,
    revenueNanm: 0n,
    grossNanm: 0n,
    cogsNanm: 0n,
    profitNanm: 0n,
    payoutsNanm: 0n,
    revenueCents: entPay._sum.amountCents ?? 0,
    marginBps: null,
    users: entPayers.length,
    developers: 0,
    executions: entPay._count._all,
  };

  return [functions, apps, agents, ai, compute, marketplace, subscriptions, enterprise];
}

// ---------------------------------------------------------------------------
// 7. Funnel (§93 — only what the data supports; no LTV/CAC, ever)
// ---------------------------------------------------------------------------

export interface FunnelReport {
  /** null: visitors are not tracked anywhere in this system — reported as unavailable. */
  visitors: null;
  registered: number; // accounts as of range end
  developers: number; // accounts owning >= 1 cloud function
  deployed: number; // accounts that created >= 1 immutable function version
  activeDevelopers: number; // developers whose functions executed in range
  paidAccounts: number; // live paid subscription at range end OR a verified capture in range
  revenueGenerating: number; // developers actually paid out in range
  freeToPaidConversionBps: number | null; // paidAccounts / registered
}

export async function funnel(range: Range): Promise<FunnelReport> {
  const inRange = within(range);
  const [registered, devOwners, deployers, activeDevs, paidSubs, paidCaptures, earning] = await Promise.all([
    prisma.account.count({ where: { createdAt: { lt: range.end } } }),
    prisma.cloudFunction.findMany({ where: { createdAt: { lt: range.end } }, distinct: ['ownerId'], select: { ownerId: true } }),
    prisma.cloudFunctionVersion.findMany({ where: { createdAt: { lt: range.end } }, distinct: ['createdById'], select: { createdById: true } }),
    prisma.cloudExecution.groupBy({ by: ['developerAccountId'], where: { createdAt: inRange } }),
    prisma.planSubscription.findMany({
      where: {
        priceUsdCents: { gt: 0 },
        createdAt: { lt: range.end },
        OR: [{ status: { in: [...MRR_STATES] } }, { status: 'CANCELED', canceledAt: { gt: range.end } }],
      },
      distinct: ['accountId'],
      select: { accountId: true },
    }),
    prisma.billingPayment.groupBy({ by: ['accountId'], where: { occurredAt: inRange, status: 'COMPLETED' } }),
    prisma.cloudExecution.groupBy({
      by: ['developerAccountId'],
      where: { createdAt: inRange, developerNanm: { gt: 0n } },
    }),
  ]);
  const paid = new Set<string>([...paidSubs.map((s) => s.accountId), ...paidCaptures.map((p) => p.accountId)]);
  return {
    visitors: null,
    registered,
    developers: devOwners.length,
    deployed: deployers.length,
    activeDevelopers: activeDevs.length,
    paidAccounts: paid.size,
    revenueGenerating: earning.length,
    freeToPaidConversionBps: registered > 0 ? Math.round((paid.size / registered) * 10_000) : null,
  };
}

// ---------------------------------------------------------------------------
// 8. Free tier report (§78)
// ---------------------------------------------------------------------------

export interface FreeTierReport {
  costNanm: bigint; // COGS of free executions in range
  executions: number;
  freeUsers: number; // distinct signed-in free-tier callers
  anonymousExecutions: number; // free executions with no account (rate-limited public calls)
  costPerFreeUserNanm: bigint | null; // null when there are no attributable free users
}

export async function freeTierReport(range: Range): Promise<FreeTierReport> {
  const inRange = within(range);
  const [agg, users, anon] = await Promise.all([
    prisma.cloudExecution.aggregate({
      where: { createdAt: inRange, freeTier: true },
      _sum: { cogsNanm: true },
      _count: { _all: true },
    }),
    prisma.cloudExecution.groupBy({
      by: ['callerAccountId'],
      where: { createdAt: inRange, freeTier: true, callerAccountId: { not: null } },
    }),
    prisma.cloudExecution.count({ where: { createdAt: inRange, freeTier: true, callerAccountId: null } }),
  ]);
  const costNanm = agg._sum.cogsNanm ?? 0n;
  return {
    costNanm,
    executions: agg._count._all,
    freeUsers: users.length,
    anonymousExecutions: anon,
    costPerFreeUserNanm: users.length > 0 ? costNanm / BigInt(users.length) : null,
  };
}

// ---------------------------------------------------------------------------
// One-call overview for the dashboard route
// ---------------------------------------------------------------------------

export interface FinanceOverview {
  range: { key: RangeKey; label: string; start: string | null; end: string };
  revenue: RevenueReport;
  cogs: CogsReport;
  profit: ProfitReport;
  unit: UnitEconomicsReport;
  usd: UsdMetricsReport;
  products: ProductLine[];
  funnel: FunnelReport;
  freeTier: FreeTierReport;
  /** Month-to-date free tier figures (§78 asks for "this month" regardless of selected range). */
  freeTierMtd: FreeTierReport;
  anmUsd: AnmUsdRef | null;
}

export async function financeOverview(key: RangeKey, now: Date = new Date()): Promise<FinanceOverview> {
  const range = rangeFor(key, now);
  const mtd = rangeFor('mtd', now);
  const [revenue, cogs, profit, unit, usd, products, fnl, free, freeMtd, price] = await Promise.all([
    revenueFor(range),
    cogsFor(range),
    profitFor(range),
    unitEconomics(range),
    usdMetrics(range),
    productBreakdown(range),
    funnel(range),
    freeTierReport(range),
    freeTierReport(mtd),
    anmUsdRef(now),
  ]);
  return {
    range: { key, label: range.label, start: range.start ? range.start.toISOString() : null, end: range.end.toISOString() },
    revenue,
    cogs,
    profit,
    unit,
    usd,
    products,
    funnel: fnl,
    freeTier: free,
    freeTierMtd: freeMtd,
    anmUsd: price,
  };
}
