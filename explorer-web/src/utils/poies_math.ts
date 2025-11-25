/* -----------------------------------------------------------------------------
 * PoIES Math Utilities
 *
 * - Γ aggregation from per-metric ψ values (providers × metrics)
 * - ψ breakdown helpers (normalization, directional tuning)
 * - Caps clipping (per-provider max share with iterative redistribution)
 * - Fairness indices (Gini, Herfindahl-Hirschman, Effective N)
 *
 * All functions are deterministic, dependency-free, and type-safe.
 * -------------------------------------------------------------------------- */

export type Matrix = number[][]; // shape: providers × metrics
export type Vector = number[];

/** Direction per metric: +1 means "higher is better", -1 means "lower is better". */
export type Direction = 1 | -1;

export interface NormalizeOpts {
  /** Per-metric direction. If omitted, all metrics are treated as higher-is-better (+1). */
  directions?: Direction[];
  /**
   * Min–max normalization guard. If a metric column is constant,
   * we set all normalized values to `constantColumnValue` (default 1.0).
   */
  constantColumnValue?: number;
  /** Small epsilon for numeric safety. */
  eps?: number;
}

/** Aggregation mode for Γ. */
export type AggregateMode = 'geom' | 'arith' | 'harm';

/** Configuration for Γ aggregation. */
export interface AggregateOpts {
  /** Metric weights (length = #metrics). If not provided, equal weights are used. */
  weights?: Vector;
  /** Aggregation mode. Default: 'geom' (weighted geometric mean). */
  mode?: AggregateMode;
  /** Safety epsilon (e.g., for geometric/harmonic means). Default: 1e-12. */
  eps?: number;
}

export interface GammaResult {
  /** Γ per provider (length = #providers). */
  gamma: Vector;
  /** ψ normalized, direction-adjusted, in [0,1]. Same shape as input. */
  psi: Matrix;
  /** Weights actually applied per metric (normalized to sum to 1). */
  weights: Vector;
}

/* -----------------------------------------------------------------------------
 * Utilities
 * -------------------------------------------------------------------------- */

/** Clamp a number to [min, max]. */
export const clamp = (x: number, min = 0, max = 1) => Math.max(min, Math.min(max, x));

/** Sum of vector (treat non-finite as 0). */
export const sum = (v: Vector) => v.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);

/** Normalize vector to a probability simplex (sum == 1). If vector is all zeros, return uniform. */
export function normalize(v: Vector, eps = 1e-18): Vector {
  const s = sum(v);
  if (s <= eps) {
    const n = v.length || 1;
    return Array.from({ length: n }, () => 1 / n);
  }
  return v.map((x) => (Number.isFinite(x) && x > 0 ? x / s : 0));
}

/** Transpose matrix. */
export function transpose(m: Matrix): Matrix {
  if (m.length === 0) return [];
  const rows = m.length;
  const cols = m[0].length;
  const out: Matrix = Array.from({ length: cols }, () => Array(rows));
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) out[j][i] = m[i][j];
  }
  return out;
}

/* -----------------------------------------------------------------------------
 * ψ Normalization & Breakdown
 * -------------------------------------------------------------------------- */

/**
 * Normalize raw metric matrix (providers × metrics) into ψ in [0,1],
 * respecting per-metric direction (higher or lower is better).
 *
 * Each metric column is min–max scaled independently:
 *   ψ_ij = (x_ij - min_j) / (max_j - min_j)  (or inverted if direction = -1)
 *
 * If a column is constant, all ψ are set to `constantColumnValue` (default 1.0).
 */
export function normalizePsi(
  raw: Matrix,
  opts: NormalizeOpts = {},
): Matrix {
  const { directions, constantColumnValue = 1.0, eps = 1e-12 } = opts;
  if (raw.length === 0) return [];
  const rows = raw.length;
  const cols = raw[0].length;

  const dir: Direction[] = directions && directions.length === cols
    ? directions
    : Array.from({ length: cols }, () => 1 as Direction);

  const colMin = Array<number>(cols).fill(Number.POSITIVE_INFINITY);
  const colMax = Array<number>(cols).fill(Number.NEGATIVE_INFINITY);

  // Column stats
  for (let i = 0; i < rows; i++) {
    const r = raw[i];
    for (let j = 0; j < cols; j++) {
      const x = Number.isFinite(r[j]) ? r[j] : 0;
      if (x < colMin[j]) colMin[j] = x;
      if (x > colMax[j]) colMax[j] = x;
    }
  }

  // Normalize
  const psi: Matrix = Array.from({ length: rows }, () => Array<number>(cols).fill(0));
  for (let j = 0; j < cols; j++) {
    const d = colMax[j] - colMin[j];
    if (Math.abs(d) <= eps) {
      for (let i = 0; i < rows; i++) psi[i][j] = clamp(constantColumnValue, 0, 1);
      continue;
    }
    for (let i = 0; i < rows; i++) {
      const base = (raw[i][j] - colMin[j]) / d; // in [0,1]
      psi[i][j] = dir[j] === 1 ? base : 1 - base;
    }
  }
  return psi;
}

/**
 * Weighted ψ breakdown per provider (contribution by each metric),
 * useful for UI charts. Weights are normalized to sum to 1.
 * Returns matrix of same shape as ψ where each cell is w_j * ψ_ij.
 */
export function weightedPsiBreakdown(
  psi: Matrix,
  weights?: Vector,
): Matrix {
  if (psi.length === 0) return [];
  const cols = psi[0].length;
  const w = normalize(weights ?? Array.from({ length: cols }, () => 1));
  return psi.map((row) => row.map((v, j) => w[j] * clamp(v, 0, 1)));
}

/* -----------------------------------------------------------------------------
 * Γ Aggregation
 * -------------------------------------------------------------------------- */

/**
 * Aggregate ψ into Γ per provider using a chosen mean:
 *  - 'geom'  : weighted geometric mean (default) — rewards balance across metrics
 *  - 'arith' : weighted arithmetic mean
 *  - 'harm'  : weighted harmonic mean — penalizes low components strongly
 */
export function aggregateGamma(
  psi: Matrix,
  options: AggregateOpts = {},
): GammaResult {
  if (psi.length === 0) return { gamma: [], psi, weights: [] };
  const rows = psi.length;
  const cols = psi[0].length;

  const { weights, mode = 'geom', eps = 1e-12 } = options;
  const w = normalize(weights ?? Array.from({ length: cols }, () => 1));

  const gamma: Vector = Array(rows).fill(0);

  if (mode === 'arith') {
    for (let i = 0; i < rows; i++) {
      let acc = 0;
      for (let j = 0; j < cols; j++) acc += w[j] * clamp(psi[i][j], 0, 1);
      gamma[i] = acc;
    }
  } else if (mode === 'harm') {
    for (let i = 0; i < rows; i++) {
      let denom = 0;
      for (let j = 0; j < cols; j++) {
        const v = Math.max(psi[i][j], eps);
        denom += w[j] / v;
      }
      gamma[i] = denom > eps ? 1 / denom : 0;
    }
  } else {
    // Geometric mean
    for (let i = 0; i < rows; i++) {
      let acc = 0;
      for (let j = 0; j < cols; j++) {
        const v = Math.max(psi[i][j], eps);
        acc += w[j] * Math.log(v);
      }
      gamma[i] = Math.exp(acc);
    }
  }

  return { gamma, psi, weights: w };
}

/* -----------------------------------------------------------------------------
 * Caps clip (iterative redistribution)
 * -------------------------------------------------------------------------- */

/**
 * Apply a per-provider cap to a set of shares (probabilities).
 * Algorithm:
 *  1. Start from a probability vector p (sum=1).
 *  2. Iteratively clip any entry above `cap` to `cap`.
 *  3. Redistribute the excess to the remaining (uncapped) entries
 *     proportionally to their current mass.
 *  4. Repeat until convergence or no entry exceeds `cap`.
 *
 * This matches common anti-concentration policies and preserves sum=1.
 */
export function clipWithCap(shares: Vector, cap = 0.2, eps = 1e-12): Vector {
  if (shares.length === 0) return [];
  cap = clamp(cap, 0, 1);
  let p = normalize(shares, eps);

  for (let iter = 0; iter < 1000; iter++) {
    const overIdx: number[] = [];
    let excess = 0;
    for (let i = 0; i < p.length; i++) {
      if (p[i] > cap + 1e-15) {
        excess += p[i] - cap;
        p[i] = cap;
        overIdx.push(i);
      }
    }
    if (excess <= 1e-15) break;

    // Compute total mass of under-cap entries
    let underMass = 0;
    for (let i = 0; i < p.length; i++) if (p[i] < cap - 1e-15) underMass += p[i];

    if (underMass <= eps) {
      // Everyone is at/near cap (or vector degenerate) — spread uniformly among under-cap or do nothing
      // In this edge case, just renormalize and break.
      p = normalize(p, eps);
      break;
    }

    // Redistribute proportionally among under-cap entries
    for (let i = 0; i < p.length; i++) {
      if (p[i] < cap - 1e-15) p[i] += (p[i] / underMass) * excess;
    }
  }

  // Final numeric hygiene
  const total = sum(p);
  if (Math.abs(total - 1) > 1e-9) p = normalize(p, eps);
  // Clamp tiny negatives due to FP errors
  p = p.map((x) => (x < 0 && x > -1e-15 ? 0 : x));
  return p;
}

/* -----------------------------------------------------------------------------
 * Fairness indices
 * -------------------------------------------------------------------------- */

/**
 * Gini coefficient for a vector of shares (need not be normalized).
 * Returns 0 for perfect equality; approaches 1 for maximal inequality.
 */
export function gini(shares: Vector, eps = 1e-18): number {
  const n = shares.length;
  if (n === 0) return 0;
  const x = shares.slice().map((v) => Math.max(0, Number.isFinite(v) ? v : 0));
  x.sort((a, b) => a - b);
  const s = sum(x);
  if (s <= eps) return 0;

  // G = (2 * sum(i * x_i)) / (n * sum x) - (n + 1)/n
  let weightedSum = 0;
  for (let i = 0; i < n; i++) weightedSum += (i + 1) * x[i];
  return (2 * weightedSum) / (n * s) - (n + 1) / n;
}

/**
 * Herfindahl-Hirschman Index (HHI).
 * - hhiRaw: sum p_i^2 (0..1, but ≥ 1/n if uniform)
 * - hhiNormalized: (HHI - 1/n) / (1 - 1/n), scaled to [0,1]
 * - effectiveN: 1 / HHI (Hill number; "effective number of providers")
 */
export function herfindahl(shares: Vector, eps = 1e-18): {
  hhiRaw: number;
  hhiNormalized: number;
  effectiveN: number;
} {
  const n = shares.length || 1;
  const p = normalize(shares, eps);
  const hhiRaw = p.reduce((a, v) => a + v * v, 0);
  const hhiNormalized = (hhiRaw - 1 / n) / (1 - 1 / n);
  const effectiveN = hhiRaw > eps ? 1 / hhiRaw : n;
  return { hhiRaw, hhiNormalized: clamp(hhiNormalized, 0, 1), effectiveN };
}

/** Convenience pack: both indices + top-k concentration. */
export function fairnessSummary(
  shares: Vector,
  k = 3,
): {
  gini: number;
  hhi: number;
  hhiNorm: number;
  effectiveN: number;
  topK: number;
} {
  const g = gini(shares);
  const { hhiRaw, hhiNormalized, effectiveN } = herfindahl(shares);
  const sorted = normalize(shares).slice().sort((a, b) => b - a);
  const topK = sum(sorted.slice(0, Math.max(0, Math.min(k, sorted.length))));
  return { gini: clamp(g, 0, 1), hhi: hhiRaw, hhiNorm: hhiNormalized, effectiveN, topK };
}

/* -----------------------------------------------------------------------------
 * From Γ to capped allocation
 * -------------------------------------------------------------------------- */

/**
 * Convert Γ into an allocation of shares with optional cap.
 * Typical pipeline:
 *   1) ψ ← normalizePsi(raw)
 *   2) Γ ← aggregateGamma(ψ, { weights, mode: 'geom' })
 *   3) shares ← allocateFromGamma(Γ, { cap: 0.2 })
 */
export function allocateFromGamma(
  gamma: Vector,
  { cap, eps = 1e-12 }: { cap?: number; eps?: number } = {},
): {
  uncapped: Vector;
  capped: Vector;
} {
  const uncapped = normalize(gamma, eps);
  const capped = typeof cap === 'number' ? clipWithCap(uncapped, cap, eps) : uncapped.slice();
  return { uncapped, capped };
}

/* -----------------------------------------------------------------------------
 * Example (pseudo)
 * --------------------------------------------------------------------------
 * const raw: Matrix = [
 *   // provider A: [throughput, latency(ms), availability(%)]
 *   [1200, 60, 99.9],
 *   // provider B:
 *   [900, 45, 99.5],
 *   // provider C:
 *   [1500, 80, 99.7],
 * ];
 * // Directions: higher throughput + availability are good; lower latency is good
 * const psi = normalizePsi(raw, { directions: [1, -1, 1] });
 * const { gamma } = aggregateGamma(psi, { weights: [0.5, 0.3, 0.2], mode: 'geom' });
 * const { capped } = allocateFromGamma(gamma, { cap: 0.4 });
 * const fairness = fairnessSummary(capped, 3);
 * -------------------------------------------------------------------------- */
