/* -----------------------------------------------------------------------------
 * PoIES Types & Helpers
 *
 * ψ (psi) inputs: per-source contribution fractions for a given block/round.
 * Γ (gamma): an evenness/mix metric in [0,1] (higher = more balanced mix).
 * Fairness: complementary indicators (Gini, Herfindahl/HHI, effective N).
 *
 * This file is intentionally dependency-free and UI-oriented. The Explorer
 * can compute and visualize mix quality from backend-provided ψ breakdowns.
 * -------------------------------------------------------------------------- */

import type { Hash } from './core';

/** Canonical list of contribution sources (can be extended safely). */
export const ALL_SOURCES = [
  'vrf',
  'commitReveal',
  'aicf',
  'quantum',
  'da',
  'validator',
  'txEntropy',
  'prevBeacon',
  'other',
] as const;

export type PsiSource = (typeof ALL_SOURCES)[number];

/** A single ψ component before normalization. */
export interface PsiComponent {
  source: PsiSource | string;     // unknown strings allowed; will be folded into 'other' if not known
  weight: number;                 // non-negative; arbitrary units (normalized later)
  entropyBits?: number;           // (optional) informational only
}

/** A structured ψ breakdown for one anchor (block/round). */
export interface PsiBreakdown {
  height?: number;
  blockHash?: Hash;
  timestamp?: number;             // unix seconds
  components: PsiComponent[];
  /**
   * Optional cap applied during normalization so one source does not dominate.
   * Example: 0.5 caps any source at 50% before re-normalization.
   */
  cap?: number;
}

/** Normalized mix shares across known sources (sums to ~1.0). */
export type MixShares = Record<PsiSource, number>;

/** Mix represented in percentages (0..100). */
export type MixPercentages = Record<PsiSource, number>;

/** Fairness indicators (lower Gini/HHI → more even; higher effectiveN → better). */
export interface Fairness {
  /** Gini coefficient in [0,1]; 0 = perfectly even, 1 = one source dominates. */
  gini: number;
  /**
   * Herfindahl–Hirschman Index in [0,1]; sum_i s_i^2.
   * 1/n ≤ HHI ≤ 1. Lower is more even.
   */
  hhi: number;
  /** Effective number of independent sources = 1/HHI. */
  effectiveN: number;
  /** Shannon entropy (nats) over non-zero shares. */
  shannonH: number;
  /** Pielou evenness J = H / ln(k), k = count of non-zero shares, in [0,1]. */
  evenness: number;
  /** Largest single share (0..1) and its source label if available. */
  maxShare: number;
  maxSource?: PsiSource;
}

/** Γ score, an alias for Pielou evenness by default (0..1). */
export type Gamma = number;

/** A compact snapshot combining ψ → Γ + fairness. */
export interface PoIESnapshot {
  height?: number;
  blockHash?: Hash;
  timestamp?: number;

  /** Normalized shares per source after optional capping. */
  shares: MixShares;
  /** Γ score in [0,1]. */
  gamma: Gamma;
  /** Fairness indicators derived from shares. */
  fairness: Fairness;
}

/* -----------------------------------------------------------------------------
 * Normalization & Utilities
 * -------------------------------------------------------------------------- */

/** Maps unknown source labels to 'other' while preserving known labels. */
function coerceSource(label: string): PsiSource {
  return (ALL_SOURCES as readonly string[]).includes(label) ? (label as PsiSource) : 'other';
}

/** Initialize an all-zero share record. */
export function zeroShares(): MixShares {
  const out: Partial<MixShares> = {};
  for (const s of ALL_SOURCES) out[s] = 0;
  return out as MixShares;
}

/**
 * Build normalized shares from ψ components. Applies:
 *  1) fold unknown labels → 'other'
 *  2) negative weights → treated as 0
 *  3) optional cap per source (e.g., 0.5) then renormalize
 */
export function normalizeShares(
  components: PsiComponent[],
  cap?: number
): MixShares {
  const raw = zeroShares();
  for (const c of components) {
    const src = coerceSource(c.source);
    const w = isFinite(c.weight) ? Math.max(0, c.weight) : 0;
    raw[src] += w;
  }
  // sum
  let total = 0;
  for (const s of ALL_SOURCES) total += raw[s];

  // handle empty
  if (total <= 0) return zeroShares();

  // normalize to [0,1]
  const norm = zeroShares();
  for (const s of ALL_SOURCES) norm[s] = raw[s] / total;

  // optionally cap & renormalize
  if (typeof cap === 'number' && cap > 0 && cap < 1) {
    let cappedSum = 0;
    const capped = zeroShares();
    for (const s of ALL_SOURCES) {
      capped[s] = Math.min(norm[s], cap);
      cappedSum += capped[s];
    }
    if (cappedSum === 0) return zeroShares();
    for (const s of ALL_SOURCES) capped[s] = capped[s] / cappedSum;
    return capped;
  }

  return norm;
}

/** Convert shares (0..1) to integer percentages (0..100) with rounding. */
export function toPercentages(shares: MixShares): MixPercentages {
  const perc: Partial<MixPercentages> = {};
  for (const s of ALL_SOURCES) perc[s] = Math.max(0, Math.min(100, Math.round(shares[s] * 100)));
  return perc as MixPercentages;
}

/* -----------------------------------------------------------------------------
 * Fairness & Γ
 * -------------------------------------------------------------------------- */

/** Gini coefficient over shares in [0,1]. */
export function gini(shares: number[]): number {
  const xs = shares.filter((x) => x > 0).sort((a, b) => a - b);
  const n = xs.length;
  if (n === 0) return 0;
  // Gini via mean absolute difference / (2 * mean)
  const mean = xs.reduce((a, b) => a + b, 0) / n; // should be <= 1 and > 0
  if (mean === 0) return 0;
  let mad = 0;
  for (let i = 0; i < n; i++) {
    for (let j = 0; j < n; j++) {
      mad += Math.abs(xs[i] - xs[j]);
    }
  }
  return mad / (2 * n * n * mean);
}

/** Herfindahl–Hirschman Index (sum of squares). */
export function hhi(shares: number[]): number {
  return shares.reduce((acc, s) => acc + s * s, 0);
}

/** Shannon entropy (natural log) over non-zero shares. */
export function shannonEntropy(shares: number[]): number {
  let h = 0;
  for (const s of shares) {
    if (s > 0) h -= s * Math.log(s);
  }
  return h;
}

/** Pielou's evenness J = H / ln(k), k = count of non-zero shares. */
export function pielouEvenness(shares: number[]): number {
  const nz = shares.filter((s) => s > 0);
  const k = nz.length;
  if (k <= 1) return 0;
  const H = shannonEntropy(nz);
  return H / Math.log(k);
}

/**
 * Compute fairness metrics + Γ (by default, Γ := Pielou evenness).
 * Returns also maxShare and its source index for convenience.
 */
export function computeFairnessAndGamma(
  sharesRec: MixShares
): { fairness: Fairness; gamma: Gamma } {
  const shares = ALL_SOURCES.map((s) => sharesRec[s]);
  const nz = shares.filter((s) => s > 0);
  const H = shannonEntropy(nz);
  const even = pielouEvenness(shares);
  const _hhi = hhi(shares);
  const effN = _hhi > 0 ? 1 / _hhi : 0;

  let maxShare = 0;
  let maxIdx = -1;
  for (let i = 0; i < shares.length; i++) {
    if (shares[i] >= maxShare) {
      maxShare = shares[i];
      maxIdx = i;
    }
  }
  const fairness: Fairness = {
    gini: gini(shares),
    hhi: _hhi,
    effectiveN: effN,
    shannonH: H,
    evenness: even,
    maxShare,
    maxSource: maxIdx >= 0 ? (ALL_SOURCES[maxIdx] as PsiSource) : undefined,
  };
  const gamma: Gamma = even; // alias by definition here
  return { fairness, gamma };
}

/* -----------------------------------------------------------------------------
 * High-level aggregator
 * -------------------------------------------------------------------------- */

export interface AggregateOptions {
  cap?: number; // optional per-source cap before normalization
}

/** From a ψ breakdown, produce a complete PoIESSnapshot. */
export function aggregatePoIES(
  b: PsiBreakdown,
  opts?: AggregateOptions
): PoIESnapshot {
  const shares = normalizeShares(b.components, opts?.cap ?? b.cap);
  const { fairness, gamma } = computeFairnessAndGamma(shares);
  return {
    height: b.height,
    blockHash: b.blockHash,
    timestamp: b.timestamp,
    shares,
    gamma,
    fairness,
  };
}

/* -----------------------------------------------------------------------------
 * Timeseries helpers (for charts & rolling windows)
 * -------------------------------------------------------------------------- */

export interface SeriesPoint<T = number> {
  t: number;       // unix seconds (preferred) OR block height if you choose
  v: T;
}

/** Basic series container for Γ and selected fairness indicators. */
export interface PoIESeries {
  gamma: SeriesPoint[];                 // Γ over time
  maxShare: SeriesPoint[];              // largest single-source share
  hhi: SeriesPoint[];                   // concentration
  effectiveN: SeriesPoint[];            // diversity proxy
}

/**
 * Push a snapshot into series buffers (immutably).
 * Caller can slice to a max length to maintain a rolling window.
 */
export function pushIntoSeries(series: PoIESeries, snap: PoIESnapshot): PoIESeries {
  const t = snap.timestamp ?? snap.height ?? Date.now() / 1000;
  return {
    gamma: [...series.gamma, { t, v: snap.gamma }],
    maxShare: [...series.maxShare, { t, v: snap.fairness.maxShare }],
    hhi: [...series.hhi, { t, v: snap.fairness.hhi }],
    effectiveN: [...series.effectiveN, { t, v: snap.fairness.effectiveN }],
  };
}

/** Create empty series. */
export function emptySeries(): PoIESeries {
  return { gamma: [], maxShare: [], hhi: [], effectiveN: [] };
}

/* -----------------------------------------------------------------------------
 * Convenience formatting
 * -------------------------------------------------------------------------- */

/** Human label for Γ (rounded%). */
export function gammaLabel(gamma: number): string {
  const pct = Math.round(Math.max(0, Math.min(1, gamma)) * 100);
  return `Γ ${pct}%`;
}

/** Convert shares record to percentages with fixed decimals. */
export function sharesToPercentStrings(
  shares: MixShares,
  fractionDigits = 1
): Record<PsiSource, string> {
  const out: Partial<Record<PsiSource, string>> = {};
  for (const s of ALL_SOURCES) {
    out[s] = (shares[s] * 100).toFixed(fractionDigits) + '%';
  }
  return out as Record<PsiSource, string>;
}
