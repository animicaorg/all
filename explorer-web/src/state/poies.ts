/**
 * Animica Explorer — PoIES analytics state
 * -----------------------------------------------------------------------------
 * Maintains time-series buffers for:
 *   - Γ (gamma): security/quality scalar in [0, 1] (as emitted by the node)
 *   - Fairness: proposer fairness (computed as 1 - Gini over recent proposer share)
 *   - Mix: entropy mix ratio in [0, 1] (contributors / expected or provided ratio)
 *
 * The store is resilient to heterogeneous block payloads. It attempts to
 * normalize fields from different node/service shapes:
 *   - height:    block.height | block.number
 *   - proposer:  block.proposer | block.header.proposer | block.miner
 *   - gamma:     block.poies.gamma | block.metrics.gamma | block.gamma
 *   - fairness:  block.poies.fairness | block.metrics.fairness | block.fairness
 *   - mix:       block.poies.mix.ratio | block.entropy.mixRatio |
 *                (contributors / committeeSize) |
 *                (entropy.contributors / entropy.committeeSize)
 *
 * When fairness is not supplied, it is computed over a sliding window
 * (default 256 blocks) as 1 - Gini(proposerShares).
 */

import { create } from 'zustand';
import { shallow } from 'zustand/shallow';

// --------------------------------- Types ------------------------------------

export type Numeric = number;

export interface SamplePoint {
  x: number;     // block height
  y: number;     // value (0..1 typically)
  t?: number;    // ms since epoch (optional)
}

export interface PoiesSeries {
  capacity: number;
  data: SamplePoint[];
}

export interface PoiesConfig {
  seriesCapacity?: number;      // ring buffer cap for series (default 1024)
  fairnessWindow?: number;      // # of recent blocks for fairness (default 256)
}

export interface PoiesState {
  gamma: PoiesSeries;
  fairness: PoiesSeries;
  mix: PoiesSeries;

  // Rolling proposer window for fairness computation
  fairnessWindow: number;
  proposerCounts: Map<string, number>;
  proposerOrder: string[]; // ring-like queue of last N proposers

  // Deduplication of processed heights
  seenHeights: Set<number>;

  // ------------- mutators -------------
  reset: () => void;
  configure: (cfg: PoiesConfig) => void;

  pushGamma: (s: SamplePoint) => void;
  pushFairness: (s: SamplePoint) => void;
  pushMix: (s: SamplePoint) => void;

  ingestBlock: (block: any) => void;
  ingestBlocks: (blocks: any[]) => void;

  // ------------- selectors -------------
  latest: () => { gamma?: SamplePoint; fairness?: SamplePoint; mix?: SamplePoint };
  rollingAvg: (series: 'gamma' | 'fairness' | 'mix', lastN?: number) => number | null;
  asArrays: () => { gamma: SamplePoint[]; fairness: SamplePoint[]; mix: SamplePoint[] };
}

// ------------------------------ Utilities -----------------------------------

function clamp01(x: number | undefined | null): number | undefined {
  if (x == null || Number.isNaN(x)) return undefined;
  if (!Number.isFinite(x)) return undefined;
  if (x < 0) return 0;
  if (x > 1) return 1;
  return x;
}

function toInt(x: any): number | undefined {
  if (typeof x === 'number' && Number.isFinite(x)) return x | 0;
  const n = Number(x);
  return Number.isFinite(n) ? (n | 0) : undefined;
}

function nowMs(): number {
  return typeof performance !== 'undefined' && (performance as any).now
    ? Math.floor((performance as any).timeOrigin + (performance as any).now())
    : Date.now();
}

function ringPush(series: PoiesSeries, point: SamplePoint): void {
  const arr = series.data;
  arr.push(point);
  if (arr.length > series.capacity) {
    arr.splice(0, arr.length - series.capacity);
  }
}

function giniCoefficient(values: number[]): number {
  // Robust Gini for non-negative values; returns 0 (equal) .. 1 (unequal)
  const v = values.filter((x) => x > 0).slice().sort((a, b) => a - b);
  const n = v.length;
  if (n === 0) return 0;
  const sum = v.reduce((a, b) => a + b, 0);
  if (sum === 0) return 0;
  let cum = 0;
  let weighted = 0;
  for (let i = 0; i < n; i++) {
    cum += v[i];
    weighted += cum;
  }
  // G = (n + 1 - 2 * (weighted / sum)) / n
  const g = (n + 1 - (2 * weighted) / sum) / n;
  return g;
}

function computeFairnessFromProposers(counts: Map<string, number>): number {
  const arr = Array.from(counts.values());
  if (arr.length === 0) return 1;
  const g = giniCoefficient(arr);
  const fairness = 1 - g; // 1 (perfectly fair) .. 0 (max inequality)
  return clamp01(fairness) ?? 0;
}

function extract(block: any): {
  height?: number;
  proposer?: string;
  gamma?: number;
  fairness?: number;
  mix?: number;
} {
  const height =
    toInt(block?.height) ??
    toInt(block?.number) ??
    toInt(block?.header?.height) ??
    toInt(block?.header?.number);

  const proposer: string | undefined =
    (block?.proposer && String(block.proposer)) ||
    (block?.header?.proposer && String(block.header.proposer)) ||
    (block?.miner && String(block.miner)) ||
    undefined;

  // Ɣ
  const gammaRaw =
    block?.poies?.gamma ??
    block?.metrics?.gamma ??
    block?.gamma ??
    undefined;
  const gamma =
    typeof gammaRaw === 'string'
      ? clamp01(parseFloat(gammaRaw))
      : clamp01(Number(gammaRaw));

  // Fairness (may be provided by node)
  const fairnessRaw =
    block?.poies?.fairness ??
    block?.metrics?.fairness ??
    block?.fairness ??
    undefined;
  const fairness =
    typeof fairnessRaw === 'string'
      ? clamp01(parseFloat(fairnessRaw))
      : clamp01(Number(fairnessRaw));

  // Mix ratio
  let mix: number | undefined;
  const mixObj = block?.poies?.mix ?? block?.mix ?? block?.entropy ?? {};
  if (mixObj && typeof mixObj === 'object') {
    const ratio =
      mixObj.ratio ??
      mixObj.mixRatio ??
      (Number.isFinite(mixObj.contributors) && Number.isFinite(mixObj.committeeSize)
        ? mixObj.contributors / (mixObj.committeeSize || 1)
        : undefined);
    mix =
      typeof ratio === 'string'
        ? clamp01(parseFloat(ratio))
        : clamp01(Number(ratio));
  } else {
    const contributors = block?.entropy?.contributors;
    const committee = block?.entropy?.committeeSize;
    if (Number.isFinite(contributors) && Number.isFinite(committee)) {
      mix = clamp01(Number(contributors) / Math.max(1, Number(committee)));
    }
  }

  return { height, proposer, gamma, fairness, mix };
}

// ------------------------------- Store --------------------------------------

const DEFAULT_SERIES_CAP = 1024;
const DEFAULT_FAIRNESS_WINDOW = 256;

export const usePoiesStore = create<PoiesState>((set, get) => ({
  gamma: { capacity: DEFAULT_SERIES_CAP, data: [] },
  fairness: { capacity: DEFAULT_SERIES_CAP, data: [] },
  mix: { capacity: DEFAULT_SERIES_CAP, data: [] },

  fairnessWindow: DEFAULT_FAIRNESS_WINDOW,
  proposerCounts: new Map<string, number>(),
  proposerOrder: [],
  seenHeights: new Set<number>(),

  reset: () =>
    set(() => ({
      gamma: { capacity: DEFAULT_SERIES_CAP, data: [] },
      fairness: { capacity: DEFAULT_SERIES_CAP, data: [] },
      mix: { capacity: DEFAULT_SERIES_CAP, data: [] },
      fairnessWindow: DEFAULT_FAIRNESS_WINDOW,
      proposerCounts: new Map<string, number>(),
      proposerOrder: [],
      seenHeights: new Set<number>(),
    })),

  configure: (cfg: PoiesConfig) =>
    set((s) => {
      const seriesCapacity = cfg.seriesCapacity ?? s.gamma.capacity ?? DEFAULT_SERIES_CAP;
      const fairnessWindow = cfg.fairnessWindow ?? s.fairnessWindow ?? DEFAULT_FAIRNESS_WINDOW;
      return {
        gamma: { capacity: seriesCapacity, data: s.gamma.data.slice(-seriesCapacity) },
        fairness: { capacity: seriesCapacity, data: s.fairness.data.slice(-seriesCapacity) },
        mix: { capacity: seriesCapacity, data: s.mix.data.slice(-seriesCapacity) },
        fairnessWindow,
      };
    }),

  pushGamma: (sp: SamplePoint) =>
    set((s) => {
      const next = { ...s.gamma, data: s.gamma.data.slice() };
      ringPush(next, sp);
      return { gamma: next };
    }),

  pushFairness: (sp: SamplePoint) =>
    set((s) => {
      const next = { ...s.fairness, data: s.fairness.data.slice() };
      ringPush(next, sp);
      return { fairness: next };
    }),

  pushMix: (sp: SamplePoint) =>
    set((s) => {
      const next = { ...s.mix, data: s.mix.data.slice() };
      ringPush(next, sp);
      return { mix: next };
    }),

  ingestBlock: (block: any) => {
    const { height, proposer, gamma, fairness, mix } = extract(block);
    if (!Number.isFinite(height!)) return;

    const s = get();

    if (s.seenHeights.has(height!)) {
      // already ingested
      return;
    }
    // Mark seen
    set((state) => {
      const nextSeen = new Set(state.seenHeights);
      nextSeen.add(height!);
      return { seenHeights: nextSeen };
    });

    const t = nowMs();

    // Update proposer window
    if (proposer) {
      const counts = new Map(get().proposerCounts);
      const order = get().proposerOrder.slice();

      // push proposer and maybe drop old
      order.push(proposer);
      counts.set(proposer, (counts.get(proposer) ?? 0) + 1);
      while (order.length > get().fairnessWindow) {
        const old = order.shift()!;
        const c = (counts.get(old) ?? 0) - 1;
        if (c <= 0) counts.delete(old);
        else counts.set(old, c);
      }

      set({ proposerCounts: counts, proposerOrder: order });
    }

    // Push Γ if provided
    if (gamma != null) {
      get().pushGamma({ x: height!, y: gamma, t });
    }

    // Fairness: prefer provided, else compute
    let fairnessVal = fairness;
    if (fairnessVal == null) {
      fairnessVal = computeFairnessFromProposers(get().proposerCounts);
    }
    get().pushFairness({ x: height!, y: fairnessVal ?? 0, t });

    // Mix ratio if available
    if (mix != null) {
      get().pushMix({ x: height!, y: mix, t });
    }
  },

  ingestBlocks: (blocks: any[]) => {
    if (!Array.isArray(blocks) || blocks.length === 0) return;
    // sort ascending by height for better window semantics
    const normalized = blocks
      .map((b) => extract(b))
      .filter((e) => Number.isFinite(e.height))
      .sort((a, b) => (a.height! - b.height!));

    for (const nb of normalized) {
      get().ingestBlock(nb);
    }
  },

  latest: () => {
    const s = get();
    return {
      gamma: s.gamma.data[s.gamma.data.length - 1],
      fairness: s.fairness.data[s.fairness.data.length - 1],
      mix: s.mix.data[s.mix.data.length - 1],
    };
  },

  rollingAvg: (series: 'gamma' | 'fairness' | 'mix', lastN = 64) => {
    const arr = get()[series].data;
    if (arr.length === 0) return null;
    const slice = arr.slice(-Math.max(1, lastN));
    const sum = slice.reduce((a, p) => a + (Number.isFinite(p.y) ? p.y : 0), 0);
    return sum / slice.length;
  },

  asArrays: () => {
    const s = get();
    return {
      gamma: s.gamma.data.slice(),
      fairness: s.fairness.data.slice(),
      mix: s.mix.data.slice(),
    };
  },
}));

// ------------------------------ Convenience ---------------------------------

/**
 * Lightweight selector hook for consumers that only need chart arrays.
 * Example:
 *   const { gamma, fairness, mix } = usePoiesSeries()
 */
export function usePoiesSeries() {
  return usePoiesStore(
    (s) => ({ gamma: s.gamma.data, fairness: s.fairness.data, mix: s.mix.data }),
    shallow
  );
}

/**
 * Helper to update from a blocks page/feed payload.
 * Pass the latest page of blocks as returned by your RPC adapter.
 */
export function updatePoiesFromBlocks(blocks: any[]) {
  usePoiesStore.getState().ingestBlocks(blocks);
}
