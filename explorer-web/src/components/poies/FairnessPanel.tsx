import React, { useMemo } from "react";
import { cn } from "../../utils/classnames";
import { FORMATS_DEFAULT, resolveTheme } from "../charts/chart.theme";
import Sparkline from "../charts/Sparkline";

/** A single observation of provider shares at a given time/block. */
export interface ProviderSharePoint {
  /** Unix ms timestamp (or any monotonically increasing number for x-axis). */
  t: number;
  /** Block height for reference. */
  height: number;
  /**
   * Provider share vector for this block (e.g., Σψ share, stake share, acceptance share).
   * Values may be raw weights; they will be normalized per-window before computing indices.
   */
  shares: Record<string, number>;
}

export interface FairnessPanelProps {
  /** Time series of provider share vectors in ascending order by time/height. */
  data: ProviderSharePoint[];
  /** Rolling window size (in number of points/blocks). Default: 120. */
  window?: number;
  /** Optional fixed theme; otherwise follow app/system. */
  themeMode?: "light" | "dark";
  className?: string;
}

/* ------------------------------ math helpers ------------------------------ */

/** Normalize an array so it sums to 1 (if possible). */
function normalize(vec: number[]): number[] {
  const s = vec.reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
  if (s <= 0) return vec.map(() => 0);
  return vec.map((v) => (Number.isFinite(v) ? v / s : 0));
}

/** Gini coefficient for non-negative values; returns 0..1. */
export function giniCoefficient(values: number[]): number {
  const x = values.filter((v) => v >= 0);
  if (x.length === 0) return 0;
  const sorted = [...x].sort((a, b) => a - b);
  const sum = sorted.reduce((a, b) => a + b, 0);
  if (sum === 0) return 0;
  const n = sorted.length;
  // G = (1/(n*sum)) * Σ ( (2i - n - 1) * x_i ), for i starting at 1
  let acc = 0;
  for (let i = 0; i < n; i++) {
    acc += (2 * (i + 1) - n - 1) * sorted[i];
  }
  const g = acc / (n * sum);
  // Guard numerical issues
  return Math.max(0, Math.min(1, g));
}

/** Herfindahl–Hirschman Index on shares (expects values already normalized or not). */
export function herfindahlHirschman(values: number[]): number {
  const s = normalize(values);
  return s.reduce((acc, v) => acc + v * v, 0); // 0..1
}

/** Aggregate shares across a window of points; returns a normalized vector. */
function aggregateWindow(points: ProviderSharePoint[]): number[] {
  if (points.length === 0) return [];
  const keys = new Set<string>();
  for (const p of points) {
    for (const k of Object.keys(p.shares)) keys.add(k);
  }
  const keyList = Array.from(keys).sort();
  const sums = keyList.map((k) =>
    points.reduce((acc, p) => acc + (Number.isFinite(p.shares[k]) ? p.shares[k] : 0), 0)
  );
  // Average across the window before normalization to avoid bias by window length
  const avg = sums.map((v) => v / points.length);
  return normalize(avg);
}

type FairnessPoint = { t: number; height: number; gini: number; hhi: number };

/* --------------------------------- component ------------------------------ */

export const FairnessPanel: React.FC<FairnessPanelProps> = ({
  data,
  window = 120,
  themeMode,
  className,
}) => {
  const theme = resolveTheme(themeMode);

  const fairnessSeries: FairnessPoint[] = useMemo(() => {
    if (!data || data.length === 0) return [];
    const res: FairnessPoint[] = [];
    for (let i = 0; i < data.length; i++) {
      const start = Math.max(0, i - window + 1);
      const slice = data.slice(start, i + 1);
      const vec = aggregateWindow(slice);
      const g = giniCoefficient(vec);
      const h = herfindahlHirschman(vec);
      res.push({ t: data[i].t, height: data[i].height, gini: g, hhi: h });
    }
    return res;
  }, [data, window]);

  const latest = fairnessSeries.length ? fairnessSeries[fairnessSeries.length - 1] : undefined;

  const giniSpark = fairnessSeries.map((p) => p.gini);
  const hhiSpark = fairnessSeries.map((p) => p.hhi);

  // Summary stats
  const giniStats = stats(giniSpark);
  const hhiStats = stats(hhiSpark);

  return (
    <section
      className={cn("rounded-lg border p-4 space-y-4", className)}
      style={{ background: theme.surface, borderColor: theme.grid }}
      aria-label="Fairness (rolling window)"
    >
      <header className="flex items-baseline justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-base font-semibold" style={{ color: theme.textPrimary }}>
            Fairness (rolling window)
          </h3>
          <p className="text-xs" style={{ color: theme.textMuted }}>
            Window: last {window} blocks • Samples: {fairnessSeries.length}
            {latest ? (
              <>
                {" "}
                • Latest height: <span className="tabular-nums">{latest.height}</span>
              </>
            ) : null}
          </p>
        </div>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Gini */}
        <MetricCard
          title="Gini coefficient"
          tooltip="0 = perfectly even; 1 = perfectly concentrated"
          value={latest ? FORMATS_DEFAULT.percent(latest.gini) : "—"}
          trend={<Sparkline data={giniSpark} height={48} />}
          stats={{
            min: FORMATS_DEFAULT.percent(giniStats.min ?? 0),
            avg: FORMATS_DEFAULT.percent(giniStats.avg ?? 0),
            max: FORMATS_DEFAULT.percent(giniStats.max ?? 0),
          }}
          theme={theme}
        />

        {/* HHI */}
        <MetricCard
          title="HHI (Herfindahl)"
          tooltip="Sum of squared shares; 1 = monopoly, lower is more competitive"
          value={latest ? FORMATS_DEFAULT.number(latest.hhi) : "—"}
          trend={<Sparkline data={hhiSpark} height={48} />}
          stats={{
            min: FORMATS_DEFAULT.number(hhiStats.min ?? 0),
            avg: FORMATS_DEFAULT.number(hhiStats.avg ?? 0),
            max: FORMATS_DEFAULT.number(hhiStats.max ?? 0),
          }}
          theme={theme}
        />
      </div>
    </section>
  );
};

/* ------------------------------- subcomponents ---------------------------- */

const MetricCard: React.FC<{
  title: string;
  tooltip?: string;
  value: string | number;
  trend: React.ReactNode;
  stats: { min: string | number; avg: string | number; max: string | number };
  theme: ReturnType<typeof resolveTheme>;
}> = ({ title, tooltip, value, trend, stats, theme }) => {
  return (
    <div
      className="rounded-md border p-3 flex flex-col gap-2"
      style={{ background: theme.surfaceAlt, borderColor: theme.grid }}
      role="group"
      aria-label={title}
      title={tooltip}
    >
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium" style={{ color: theme.textPrimary }}>
          {title}
        </div>
        <div
          className="text-lg font-semibold tabular-nums"
          style={{ color: theme.textEmphasis }}
          aria-live="polite"
        >
          {value}
        </div>
      </div>
      <div>{trend}</div>
      <div
        className="grid grid-cols-3 text-xs gap-2"
        style={{ color: theme.textMuted }}
        aria-label={`${title} stats`}
      >
        <div className="flex items-center justify-between">
          <span>Min</span>
          <span className="tabular-nums" style={{ color: theme.textPrimary }}>
            {stats.min}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span>Avg</span>
          <span className="tabular-nums" style={{ color: theme.textPrimary }}>
            {stats.avg}
          </span>
        </div>
        <div className="flex items-center justify-between">
          <span>Max</span>
          <span className="tabular-nums" style={{ color: theme.textPrimary }}>
            {stats.max}
          </span>
        </div>
      </div>
    </div>
  );
};

/* --------------------------------- utils ---------------------------------- */

function stats(xs: number[]) {
  if (!xs.length) return { min: undefined, avg: undefined, max: undefined };
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  let s = 0;
  let n = 0;
  for (const v of xs) {
    if (!Number.isFinite(v)) continue;
    n++;
    s += v;
    if (v < min) min = v;
    if (v > max) max = v;
  }
  const avg = n ? s / n : undefined;
  if (!n) return { min: undefined, avg: undefined, max: undefined };
  return { min, avg, max };
}

export default FairnessPanel;
