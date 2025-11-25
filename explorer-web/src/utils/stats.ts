/* -----------------------------------------------------------------------------
 * Stats utilities for Explorer:
 *  - TPS from block stream
 *  - Inter-block jitter (interval stats)
 *  - Mempool rates (arrival, drain, utilization)
 *
 * Pure, dependency-free, TypeScript-first.
 * -------------------------------------------------------------------------- */

export type BlockSample = {
  /** Block height/number (optional but helpful for per-interval TPS). */
  number?: number;
  /** Block timestamp (seconds since epoch; ms also accepted and auto-normalized). */
  timestamp: number;
  /** Number of transactions in the block. */
  txCount: number;
};

export type PendingSample = {
  /** Wall-clock time of the sample; seconds (ms accepted and auto-normalized). */
  t: number;
  /** Mempool pending transaction count at time t. */
  pending: number;
};

export type TPSInterval = {
  fromTime: number;
  toTime: number;
  dt: number;
  tx: number;
  tps: number;
  fromHeight?: number;
  toHeight?: number;
};

export type TPSStats = {
  /** Average TPS over the window [first, last]. */
  avg: number;
  /** Total transactions across the window. */
  totalTx: number;
  /** Window duration in seconds. */
  windowSec: number;
  /** Per-interval TPS between consecutive blocks (skips zero/negative dt). */
  byInterval: TPSInterval[];
  /** Min/Max per-interval TPS (ignores zero-dt). */
  min?: number;
  max?: number;
};

export type InterBlockStats = {
  /** Inter-block intervals in seconds (length = blocks - 1). */
  intervals: number[];
  /** Mean of intervals. */
  mean: number;
  /** Standard deviation of intervals. */
  stddev: number;
  /** Median absolute deviation (MAD). */
  mad: number;
  /** Coefficient of variation (stddev / mean). */
  cv: number;
  /** Quantiles. */
  p50: number;
  p75: number;
  p90: number;
  p95: number;
  p99: number;
  min: number;
  max: number;
};

export type MempoolRateStats = {
  /** Average arrival rate (tx/sec) estimated from positive ∆pending. */
  arrivalPerSec: number;
  /**
   * Average drain rate (tx/sec).
   * - If blocks were provided, equals TPS.avg from blocks.
   * - Otherwise, estimated from negative ∆pending magnitudes.
   */
  drainPerSec: number;
  /**
   * Utilization estimate: arrival / drain (0..∞), clipped to [0, 10] for display sanity.
   * Values > 1 imply backlog growth in the observed window.
   */
  utilization: number;
  /** Net mempool slope in tx/sec (arrival - drain by pending-delta method). */
  netSlopePerSec: number;
  /** Time range covered by samples (seconds). */
  windowSec: number;
};

/* -----------------------------------------------------------------------------
 * Helpers
 * -------------------------------------------------------------------------- */

function isMs(t: number) {
  return t > 1e12; // naive heuristic
}
function toSec(t: number) {
  return isMs(t) ? t / 1000 : t;
}
function safeSortBy<T>(arr: T[], key: (x: T) => number): T[] {
  return arr.slice().sort((a, b) => key(a) - key(b));
}
function diff(a: number, b: number) {
  return a - b;
}
function clamp(x: number, lo = 0, hi = 1) {
  return Math.max(lo, Math.min(hi, x));
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const p = clamp(q, 0, 1);
  const idx = (sorted.length - 1) * p;
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  const w = idx - lo;
  return sorted[lo] * (1 - w) + sorted[hi] * w;
}

function mean(xs: number[]) {
  if (xs.length === 0) return 0;
  return xs.reduce((a, b) => a + b, 0) / xs.length;
}

function stddev(xs: number[]) {
  if (xs.length <= 1) return 0;
  const m = mean(xs);
  const v = xs.reduce((a, b) => a + (b - m) * (b - m), 0) / (xs.length - 1);
  return Math.sqrt(v);
}

function median(xs: number[]) {
  if (xs.length === 0) return 0;
  const s = xs.slice().sort((a, b) => a - b);
  return quantile(s, 0.5);
}

function mad(xs: number[]) {
  if (xs.length === 0) return 0;
  const m = median(xs);
  const devs = xs.map((x) => Math.abs(x - m)).sort((a, b) => a - b);
  return quantile(devs, 0.5);
}

/* -----------------------------------------------------------------------------
 * TPS from blocks
 * -------------------------------------------------------------------------- */

/**
 * Compute TPS over a sequence of blocks. Blocks can be unordered and may contain
 * millisecond timestamps — both are normalized.
 */
export function tpsFromBlocks(blocks: BlockSample[]): TPSStats {
  const data = safeSortBy(
    blocks.map((b) => ({ ...b, t: toSec(b.timestamp) })),
    (x) => x.t,
  ).filter((b) => Number.isFinite(b.t) && Number.isFinite(b.txCount));

  if (data.length === 0) return { avg: 0, totalTx: 0, windowSec: 0, byInterval: [] };

  const first = data[0];
  const last = data[data.length - 1];

  const byInterval: TPSInterval[] = [];
  let tpsMin: number | undefined;
  let tpsMax: number | undefined;
  let totalTx = 0;

  for (let i = 1; i < data.length; i++) {
    const a = data[i - 1];
    const b = data[i];
    const dt = Math.max(0, b.t - a.t);
    const tx = b.txCount; // attribute tx to interval ending at block b
    totalTx += tx;
    if (dt > 0) {
      const tps = tx / dt;
      tpsMin = tpsMin === undefined ? tps : Math.min(tpsMin, tps);
      tpsMax = tpsMax === undefined ? tps : Math.max(tpsMax, tps);
      byInterval.push({
        fromTime: a.t,
        toTime: b.t,
        dt,
        tx,
        tps,
        fromHeight: a.number,
        toHeight: b.number,
      });
    }
  }

  const windowSec = Math.max(0, last.t - first.t);
  const avg = windowSec > 0 ? totalTx / windowSec : 0;

  return {
    avg,
    totalTx,
    windowSec,
    byInterval,
    min: tpsMin,
    max: tpsMax,
  };
}

/* -----------------------------------------------------------------------------
 * Inter-block jitter
 * -------------------------------------------------------------------------- */

/**
 * Compute inter-block timing statistics (seconds).
 * Jitter is represented by stddev and MAD (median absolute deviation).
 */
export function interBlockJitter(blocks: BlockSample[]): InterBlockStats {
  const times = safeSortBy(blocks, (b) => toSec(b.timestamp)).map((b) => toSec(b.timestamp));
  const intervals: number[] = [];
  for (let i = 1; i < times.length; i++) {
    const dt = times[i] - times[i - 1];
    if (Number.isFinite(dt) && dt > 0) intervals.push(dt);
  }
  const s = intervals.slice().sort((a, b) => a - b);
  const m = mean(s);
  const sd = stddev(s);
  const md = mad(s);
  const cv = m > 0 ? sd / m : 0;
  return {
    intervals: s,
    mean: m,
    stddev: sd,
    mad: md,
    cv,
    p50: quantile(s, 0.5),
    p75: quantile(s, 0.75),
    p90: quantile(s, 0.9),
    p95: quantile(s, 0.95),
    p99: quantile(s, 0.99),
    min: s.length ? s[0] : 0,
    max: s.length ? s[s.length - 1] : 0,
  };
}

/* -----------------------------------------------------------------------------
 * Mempool rate estimates
 * -------------------------------------------------------------------------- */

/**
 * Estimate mempool arrival & drain rates from a pending-count time series.
 * Optionally combine with block TPS to refine the drain rate.
 *
 * By default (no blocks), the method:
 *   - arrivalPerSec ≈ sum(max(∆pending, 0)) / sum(∆t)
 *   - drainPerSec   ≈ sum(max(-∆pending, 0)) / sum(∆t)
 * With blocks, drainPerSec is replaced by TPS.avg from blocks.
 */
export function mempoolRates(
  pending: PendingSample[],
  blocks?: BlockSample[],
): MempoolRateStats {
  const series = safeSortBy(
    pending.map((p) => ({ t: toSec(p.t), pending: p.pending })),
    (x) => x.t,
  ).filter((p) => Number.isFinite(p.t) && Number.isFinite(p.pending));

  if (series.length <= 1) {
    const tps = blocks ? tpsFromBlocks(blocks) : undefined;
    return {
      arrivalPerSec: 0,
      drainPerSec: tps?.avg ?? 0,
      utilization: 0,
      netSlopePerSec: 0,
      windowSec: 0,
    };
  }

  let pos = 0; // sum of positive deltas
  let neg = 0; // sum of negative deltas (magnitude)
  let sumDt = 0;

  for (let i = 1; i < series.length; i++) {
    const a = series[i - 1];
    const b = series[i];
    const dt = Math.max(0, b.t - a.t);
    if (dt <= 0) continue;
    const dp = b.pending - a.pending;
    sumDt += dt;
    if (dp >= 0) pos += dp;
    else neg += -dp;
  }

  const arrivalPerSec = sumDt > 0 ? pos / sumDt : 0;
  const drainByPending = sumDt > 0 ? neg / sumDt : 0;

  const tpsStats = blocks ? tpsFromBlocks(blocks) : undefined;
  const drainPerSec = tpsStats ? tpsStats.avg : drainByPending;

  const netSlopePerSec = arrivalPerSec - (tpsStats ? tpsStats.avg : drainByPending);
  const utilization = drainPerSec > 0 ? clamp(arrivalPerSec / drainPerSec, 0, 10) : 0;

  return {
    arrivalPerSec,
    drainPerSec,
    utilization,
    netSlopePerSec,
    windowSec: sumDt,
  };
}

/* -----------------------------------------------------------------------------
 * Convenience: short rolling-rate calculator
 * -------------------------------------------------------------------------- */

/**
 * Compute a simple rolling rate (per second) over the last `windowSec` of a series.
 * Useful for live dashboards. Falls back to whole series if too sparse.
 */
export function rollingRate(
  points: Array<{ t: number; value: number }>,
  windowSec = 30,
): number {
  const s = safeSortBy(points.map((p) => ({ t: toSec(p.t), v: p.value })), (x) => x.t);
  if (s.length <= 1) return 0;
  const endT = s[s.length - 1].t;
  const startT = endT - Math.max(1, windowSec);

  // Find first point at/after startT
  let i0 = 0;
  while (i0 < s.length - 1 && s[i0 + 1].t < startT) i0++;
  const a = s[i0];
  const b = s[s.length - 1];

  const dv = b.v - a.v;
  const dt = Math.max(1e-6, b.t - a.t);
  return dv / dt;
}
