// Animica Python Cloud — the pricing, cost-accounting and margin engine (§9, §74, §75, §76).
//
// Three separate questions, deliberately separate functions:
//   1. quote()        — what does the CUSTOMER pay?
//   2. costOf()       — what does it cost ANIMICA to serve? (COGS, never customer-visible)
//   3. splitOf()      — how is the customer's payment divided?
//
// and one guard:
//   4. applyMinMargin() — refuse to sell subsidized compute below the configured margin.
//
// Every number is integer nANM. Rounding is always floor-toward-zero via bps(), and the split
// is computed so the parts sum EXACTLY to the price (developer takes the remainder), which is
// what makes `priceNanm = platformFee + developer + provider` an invariant we can assert in
// tests and in the reconciliation job rather than a hope.

import { prisma } from '../db';
import { bps } from '../nanm';
import { economics, limits } from './config';

// ---------------------------------------------------------------------------
// Policy resolution
// ---------------------------------------------------------------------------

export interface Policy {
  id: string | null;
  version: number;
  baseCallNanm: bigint;
  cpuMsNanm: bigint;
  memMbMsNanm: bigint;
  aiTokenInNanm: bigint;
  aiTokenOutNanm: bigint;
  egressKbNanm: bigint;
  gpuMsNanm: bigint;
  platformFeeBps: number;
  providerShareBps: number;
  costCpuMsNanm: bigint;
  costMemMbMsNanm: bigint;
  costAiTokenNanm: bigint;
  costEgressKbNanm: bigint;
  costPerCallNanm: bigint;
  targetMarginBps: number;
  enforceMinMargin: boolean;
  freeExecutionsPerDay: number;
  freeExecutionsPerMonth: number;
  freeAiTokensPerDay: number;
  freeTierMonthlyCeilingNanm: bigint;
  anmUsdFloorMicros: bigint;
}

// The env-configured bootstrap policy, used when no PricingPolicy row is active yet.
export function defaultPolicy(): Policy {
  return {
    id: null,
    version: 0,
    baseCallNanm: economics.baseCallNanm,
    cpuMsNanm: economics.cpuMsNanm,
    memMbMsNanm: economics.memMbMsNanm,
    aiTokenInNanm: economics.aiTokenInNanm,
    aiTokenOutNanm: economics.aiTokenOutNanm,
    egressKbNanm: economics.egressKbNanm,
    gpuMsNanm: economics.gpuMsNanm,
    platformFeeBps: economics.platformFeeBps,
    providerShareBps: economics.providerShareBps,
    costCpuMsNanm: economics.costCpuMsNanm,
    costMemMbMsNanm: economics.costMemMbMsNanm,
    costAiTokenNanm: economics.costAiTokenNanm,
    costEgressKbNanm: economics.costEgressKbNanm,
    costPerCallNanm: economics.costPerCallNanm,
    targetMarginBps: economics.targetMarginBps,
    enforceMinMargin: economics.enforceMinMargin,
    freeExecutionsPerDay: economics.freeExecutionsPerDay,
    freeExecutionsPerMonth: economics.freeExecutionsPerMonth,
    freeAiTokensPerDay: economics.freeAiTokensPerDay,
    freeTierMonthlyCeilingNanm: economics.freeTierMonthlyCeilingNanm,
    anmUsdFloorMicros: economics.anmUsdFloorMicros,
  };
}

let cached: { at: number; policy: Policy } | null = null;
const POLICY_TTL_MS = 15_000;

/** The active pricing policy. Cached briefly so a hot execution path does not hit the DB per call. */
export async function activePolicy(): Promise<Policy> {
  const now = Date.now();
  if (cached && now - cached.at < POLICY_TTL_MS) return cached.policy;
  let policy = defaultPolicy();
  try {
    const row = await prisma.pricingPolicy.findFirst({ where: { active: true }, orderBy: { version: 'desc' } });
    if (row) {
      policy = {
        id: row.id,
        version: row.version,
        baseCallNanm: row.baseCallNanm,
        cpuMsNanm: row.cpuMsNanm,
        memMbMsNanm: row.memMbMsNanm,
        aiTokenInNanm: row.aiTokenInNanm,
        aiTokenOutNanm: row.aiTokenOutNanm,
        egressKbNanm: row.egressKbNanm,
        gpuMsNanm: row.gpuMsNanm,
        platformFeeBps: row.platformFeeBps,
        providerShareBps: row.providerShareBps,
        costCpuMsNanm: row.costCpuMsNanm,
        costMemMbMsNanm: row.costMemMbMsNanm,
        costAiTokenNanm: row.costAiTokenNanm,
        costEgressKbNanm: row.costEgressKbNanm,
        costPerCallNanm: row.costPerCallNanm,
        targetMarginBps: row.targetMarginBps,
        enforceMinMargin: row.enforceMinMargin,
        freeExecutionsPerDay: row.freeExecutionsPerDay,
        freeExecutionsPerMonth: row.freeExecutionsPerMonth,
        freeAiTokensPerDay: row.freeAiTokensPerDay,
        freeTierMonthlyCeilingNanm: row.freeTierMonthlyCeilingNanm,
        anmUsdFloorMicros: row.anmUsdFloorMicros,
      };
    }
  } catch {
    // DB unavailable: fall back to the env policy rather than failing the execution. The
    // policyId recorded on the execution will be null, which the reconciler reports.
  }
  cached = { at: now, policy };
  return policy;
}

export function invalidatePolicyCache() {
  cached = null;
}

// ---------------------------------------------------------------------------
// Usage
// ---------------------------------------------------------------------------

export interface Usage {
  cpuMs: number;
  memoryMbMs: bigint;
  aiTokensIn: number;
  aiTokensOut: number;
  egressBytes: number;
  gpuMs?: number;
}

export const ZERO_USAGE: Usage = { cpuMs: 0, memoryMbMs: 0n, aiTokensIn: 0, aiTokensOut: 0, egressBytes: 0, gpuMs: 0 };

function big(n: number | bigint): bigint {
  return typeof n === 'bigint' ? n : BigInt(Math.max(0, Math.trunc(n)));
}

/** Bytes -> whole KB, rounded up: a 1-byte response still costs 1 KB of egress. */
function kb(bytes: number): bigint {
  return bytes <= 0 ? 0n : BigInt(Math.ceil(bytes / 1024));
}

// ---------------------------------------------------------------------------
// 1. Customer price
// ---------------------------------------------------------------------------

export interface PriceBreakdown {
  baseNanm: bigint;
  cpuNanm: bigint;
  memoryNanm: bigint;
  aiNanm: bigint;
  egressNanm: bigint;
  gpuNanm: bigint;
  surchargeNanm: bigint; // developer's per-call surcharge
  meteredNanm: bigint; // everything above, before the margin floor
  floorNanm: bigint; // minimum price required to hit the target margin
  raisedByMargin: boolean;
  totalNanm: bigint;
}

export interface QuoteOpts {
  /** Developer's fixed per-call surcharge (CloudFunction.perCallNanm). */
  surchargeNanm?: bigint;
  /** True when a fleet provider will run this (adds the provider share to the split). */
  fleet?: boolean;
  /** Fee rate that will apply — needed for the margin floor. Defaults to the policy rate. */
  feeBps?: number;
}

/** What the customer pays for a given measured (or estimated) usage. */
export function quote(usage: Usage, policy: Policy, opts: QuoteOpts = {}): PriceBreakdown {
  const baseNanm = policy.baseCallNanm;
  const cpuNanm = big(usage.cpuMs) * policy.cpuMsNanm;
  const memoryNanm = big(usage.memoryMbMs) * policy.memMbMsNanm;
  const aiNanm = big(usage.aiTokensIn) * policy.aiTokenInNanm + big(usage.aiTokensOut) * policy.aiTokenOutNanm;
  const egressNanm = kb(usage.egressBytes) * policy.egressKbNanm;
  const gpuNanm = big(usage.gpuMs ?? 0) * policy.gpuMsNanm;
  const surchargeNanm = opts.surchargeNanm && opts.surchargeNanm > 0n ? opts.surchargeNanm : 0n;

  const meteredNanm = baseNanm + cpuNanm + memoryNanm + aiNanm + egressNanm + gpuNanm + surchargeNanm;

  const cogs = costOf(usage, policy);
  const feeBps = opts.feeBps ?? policy.platformFeeBps;
  const floorNanm = policy.enforceMinMargin ? minPriceForMargin(cogs.totalNanm, feeBps, policy.targetMarginBps) : 0n;

  const totalNanm = meteredNanm >= floorNanm ? meteredNanm : floorNanm;
  return {
    baseNanm,
    cpuNanm,
    memoryNanm,
    aiNanm,
    egressNanm,
    gpuNanm,
    surchargeNanm,
    meteredNanm,
    floorNanm,
    raisedByMargin: totalNanm > meteredNanm,
    totalNanm,
  };
}

// ---------------------------------------------------------------------------
// 2. Animica's cost (COGS) — internal only
// ---------------------------------------------------------------------------

export interface CostBreakdown {
  computeNanm: bigint; // CPU + memory
  aiNanm: bigint;
  infraNanm: bigint; // fixed per-call allocation + egress
  totalNanm: bigint;
}

/**
 * Direct variable cost of serving one execution. Used for COGS, gross margin and the
 * negative-margin alert. NEVER shown to customers (§74).
 */
export function costOf(usage: Usage, policy: Policy): CostBreakdown {
  const computeNanm = big(usage.cpuMs) * policy.costCpuMsNanm + big(usage.memoryMbMs) * policy.costMemMbMsNanm;
  const aiNanm = (big(usage.aiTokensIn) + big(usage.aiTokensOut)) * policy.costAiTokenNanm;
  const infraNanm = policy.costPerCallNanm + kb(usage.egressBytes) * policy.costEgressKbNanm;
  return { computeNanm, aiNanm, infraNanm, totalNanm: computeNanm + aiNanm + infraNanm };
}

// ---------------------------------------------------------------------------
// 3. Split
// ---------------------------------------------------------------------------

export interface Split {
  platformFeeNanm: bigint;
  developerNanm: bigint;
  providerNanm: bigint;
  feeBps: number;
  providerShareBps: number;
}

export class PricingError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

/**
 * Divide a customer payment. The parts always sum EXACTLY to `priceNanm`: platform and
 * provider are computed by bps, and the developer takes the remainder, so integer rounding
 * can never mint or lose a nANM.
 *
 * `feeBps` is passed in (not read from the policy) because it is a per-sale SNAPSHOT: a
 * Founding Developer's 10% rate, or the rate that was active when a historical row was
 * written, must not be rewritten by a later policy change (§88).
 */
export function splitOf(priceNanm: bigint, feeBps: number, providerShareBps: number, fleet: boolean): Split {
  if (priceNanm < 0n) throw new PricingError('BAD_PRICE', 'price must be >= 0');
  if (!Number.isInteger(feeBps) || feeBps < 0 || feeBps > 10_000) {
    throw new PricingError('BAD_FEE', `invalid feeBps ${feeBps}`);
  }
  const provBps = fleet ? providerShareBps : 0;
  if (!Number.isInteger(provBps) || provBps < 0 || provBps > 10_000) {
    throw new PricingError('BAD_PROVIDER_SHARE', `invalid providerShareBps ${provBps}`);
  }
  // §89: provider compensation may never exceed the revenue allocated to compute.
  if (feeBps + provBps > 10_000) {
    throw new PricingError('BAD_SPLIT', `feeBps ${feeBps} + providerShareBps ${provBps} exceeds 100%`);
  }
  const platformFeeNanm = bps(priceNanm, feeBps);
  const providerNanm = bps(priceNanm, provBps);
  const developerNanm = priceNanm - platformFeeNanm - providerNanm;
  if (developerNanm < 0n) throw new PricingError('BAD_SPLIT', 'split exceeds price');
  return { platformFeeNanm, developerNanm, providerNanm, feeBps, providerShareBps: provBps };
}

// ---------------------------------------------------------------------------
// 4. Margin
// ---------------------------------------------------------------------------

/**
 * The smallest customer price at which Animica's share still clears the target gross margin.
 *
 * Animica's revenue on an execution is the platform fee, not the whole price:
 *     revenue = price * feeBps/10000
 *     margin  = (revenue - cogs) / revenue >= target
 *  => revenue >= cogs / (1 - target)
 *  => price   >= cogs * 10000 / ((1 - target) * feeBps)
 *
 * Integer math, rounded UP, so the floor never lands a hair under target.
 */
export function minPriceForMargin(cogsNanm: bigint, feeBps: number, targetMarginBps: number): bigint {
  if (cogsNanm <= 0n) return 0n;
  if (feeBps <= 0) return 0n; // no platform revenue configured: nothing to protect
  const inverse = BigInt(10_000 - Math.min(targetMarginBps, 9_999)); // (1 - target) in bps
  const denom = inverse * BigInt(feeBps); // bps^2
  const numer = cogsNanm * 10_000n * 10_000n;
  return (numer + denom - 1n) / denom; // ceil
}

export interface MarginReport {
  revenueNanm: bigint; // Animica's platform fee
  cogsNanm: bigint;
  contributionNanm: bigint; // revenue - cogs (may be negative)
  marginBps: number; // contribution / revenue, in bps; 0 when revenue is 0
  belowTarget: boolean;
  negative: boolean;
}

export function marginOf(platformFeeNanm: bigint, cogsNanm: bigint, targetMarginBps: number): MarginReport {
  const contributionNanm = platformFeeNanm - cogsNanm;
  const marginBps =
    platformFeeNanm > 0n ? Number((contributionNanm * 10_000n) / platformFeeNanm) : contributionNanm < 0n ? -10_000 : 0;
  return {
    revenueNanm: platformFeeNanm,
    cogsNanm,
    contributionNanm,
    marginBps,
    belowTarget: marginBps < targetMarginBps,
    negative: contributionNanm < 0n,
  };
}

// ---------------------------------------------------------------------------
// Estimation (pre-execution) — what the developer/caller is shown before running
// ---------------------------------------------------------------------------

export interface EstimateInput {
  timeoutMs: number;
  memoryMb: number;
  expectedAiTokens?: number;
  surchargeNanm?: bigint;
  feeBps?: number;
  fleet?: boolean;
}

/**
 * A pre-execution estimate. Deliberately conservative: it assumes a quarter of the timeout
 * budget is actually consumed (the same heuristic animica.studio's billing module uses), and
 * it returns the WORST case too so a caller can size an authorization that will not fail.
 */
export function estimate(input: EstimateInput, policy: Policy) {
  const typicalMs = Math.max(1, Math.round(input.timeoutMs * 0.25));
  const mk = (ms: number, aiTokens: number): Usage => ({
    cpuMs: ms,
    memoryMbMs: BigInt(ms) * BigInt(input.memoryMb),
    aiTokensIn: Math.round(aiTokens * 0.7),
    aiTokensOut: Math.round(aiTokens * 0.3),
    egressBytes: 4096,
    gpuMs: 0,
  });
  const opts: QuoteOpts = { surchargeNanm: input.surchargeNanm, feeBps: input.feeBps, fleet: input.fleet };
  const typical = quote(mk(typicalMs, input.expectedAiTokens ?? 0), policy, opts);
  const worst = quote(
    mk(input.timeoutMs, Math.max(input.expectedAiTokens ?? 0, limits.maxAiTokensPerExecution)),
    policy,
    opts,
  );
  return { typicalNanm: typical.totalNanm, maxNanm: worst.totalNanm, typical, worst };
}

// ---------------------------------------------------------------------------
// Failure pricing (§46)
// ---------------------------------------------------------------------------

/**
 * What to charge when an execution did not succeed.
 *
 * The documented economic model: a caller pays for resources their request actually consumed,
 * never for a successful-execution outcome that did not happen.
 *   - REJECTED / QUEUED-and-cancelled  -> nothing ran, charge nothing
 *   - FAILED / TIMEOUT                 -> real CPU/memory/AI were burned, so charge the
 *                                         metered resource cost with NO developer surcharge
 *                                         and NO margin floor (Animica eats the difference
 *                                         rather than profiting from a broken function)
 */
export function priceForFailure(usage: Usage, policy: Policy): bigint {
  const b = quote(usage, policy, { surchargeNanm: 0n });
  return b.meteredNanm;
}
