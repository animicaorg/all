import React, { useMemo } from "react";
import { cn } from "../../utils/classnames";
import { FORMATS_DEFAULT, resolveTheme } from "../charts/chart.theme";

/** A single Γ observation for a block. */
export interface GammaPoint {
  /** Unix ms timestamp (or monotonically increasing). */
  t: number;
  /** Block height. */
  height: number;
  /** Γ value for this block (e.g., Σψ accepted / cap or similar unitless ratio). */
  gamma: number;
}

export interface GammaPanelProps {
  /** Per-block Γ observations, ascending by time/height. */
  data: GammaPoint[];
  /**
   * Θ target (unitless). This is rendered as a horizontal guideline and
   * compared against the EMA(Γ). Example: 0.67
   */
  theta: number;
  /**
   * EMA smoothing factor (0..1). Higher = more reactive. Default: 0.1 (~N≈19).
   * N ≈ (2/α) - 1  (approximate window size equivalence)
   */
  alpha?: number;
  /** Chart height in px (responsive width). */
  height?: number;
  /** Optional fixed theme; otherwise follow app/system. */
  themeMode?: "light" | "dark";
  className?: string;
}

/* --------------------------------- helpers -------------------------------- */

function ema(series: number[], alpha: number): number[] {
  if (!series.length) return [];
  const out = new Array(series.length);
  let e = series[0];
  out[0] = e;
  for (let i = 1; i < series.length; i++) {
    e = alpha * series[i] + (1 - alpha) * e;
    out[i] = e;
  }
  return out;
}

function pathForLine(xs: number[], ys: number[]): string {
  if (xs.length === 0) return "";
  let d = `M ${xs[0]} ${ys[0]}`;
  for (let i = 1; i < xs.length; i++) d += ` L ${xs[i]} ${ys[i]}`;
  return d;
}

function extent(values: number[]): { min: number; max: number } | undefined {
  const v = values.filter((x) => Number.isFinite(x));
  if (!v.length) return undefined;
  let min = v[0],
    max = v[0];
  for (let i = 1; i < v.length; i++) {
    if (v[i] < min) min = v[i];
    if (v[i] > max) max = v[i];
  }
  return { min, max };
}

/* -------------------------------- component -------------------------------- */

export const GammaPanel: React.FC<GammaPanelProps> = ({
  data,
  theta,
  alpha = 0.1,
  height = 180,
  themeMode,
  className,
}) => {
  const theme = resolveTheme(themeMode);

  const values = useMemo(() => data.map((d) => d.gamma), [data]);
  const emaSeries = useMemo(() => ema(values, alpha), [values, alpha]);

  const latest = data.length ? data[data.length - 1] : undefined;
  const latestEma = emaSeries.length ? emaSeries[emaSeries.length - 1] : undefined;

  // Scales for responsive SVG
  const { xs, yGamma, yEma, yTheta, yTicks, yFmt } = useMemo(() => {
    const W = 640; // logical width for viewBox
    const H = height;
    const padL = 8,
      padR = 8,
      padT = 12,
      padB = 20;
    const innerW = W - padL - padR;
    const innerH = H - padT - padB;

    const n = data.length;
    const xs = Array.from({ length: n }, (_, i) =>
      n > 1 ? padL + (i * innerW) / (n - 1) : padL + innerW / 2
    );

    const exGamma = extent(values) ?? { min: 0, max: 1 };
    const exEma = extent(emaSeries) ?? exGamma;
    const minY = Math.min(exGamma.min, exEma.min, theta);
    const maxY = Math.max(exGamma.max, exEma.max, theta);

    // add padding
    const pad = (maxY - minY || 1) * 0.1;
    const y0 = minY - pad;
    const y1 = maxY + pad;

    const yScale = (v: number) => padT + innerH - ((v - y0) / (y1 - y0)) * innerH;

    const yGamma = values.map((v) => yScale(v));
    const yEma = emaSeries.map((v) => yScale(v));
    const yTheta = yScale(theta);

    // choose ~4 ticks
    const ticks = 4;
    const yTicks = Array.from({ length: ticks + 1 }, (_, i) => {
      const v = y0 + ((y1 - y0) * i) / ticks;
      return { y: yScale(v), v };
    });

    return {
      xs,
      yGamma,
      yEma,
      yTheta,
      yTicks,
      yFmt: FORMATS_DEFAULT.number,
    };
  }, [data, values, emaSeries, theta, height]);

  const ratio = latestEma !== undefined && theta > 0 ? latestEma / theta : undefined;
  const deltaPct =
    latestEma !== undefined && theta > 0 ? (latestEma - theta) / theta : undefined;

  return (
    <section
      className={cn("rounded-lg border p-4 space-y-4", className)}
      style={{ background: theme.surface, borderColor: theme.grid }}
      aria-label="Gamma per block and EMA vs Theta"
    >
      <header className="flex items-baseline justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-base font-semibold" style={{ color: theme.textPrimary }}>
            Γ per block &amp; EMA target vs Θ
          </h3>
          <p className="text-xs" style={{ color: theme.textMuted }}>
            EMA α={FORMATS_DEFAULT.number(alpha)} • Points: {data.length}
            {latest ? (
              <>
                {" "}
                • Latest height: <span className="tabular-nums">{latest.height}</span>
              </>
            ) : null}
          </p>
        </div>
        <div className="grid grid-cols-3 gap-4 text-right">
          <Stat
            label="Latest Γ"
            value={
              latest ? FORMATS_DEFAULT.number(latest.gamma) : "—"
            }
            themeColor={theme.textEmphasis}
          />
          <Stat
            label="EMA(Γ)"
            value={
              latestEma !== undefined
                ? FORMATS_DEFAULT.number(latestEma)
                : "—"
            }
            themeColor={theme.textPrimary}
          />
          <Stat
            label="Θ target"
            value={FORMATS_DEFAULT.number(theta)}
            themeColor={theme.textPrimary}
          />
        </div>
      </header>

      {/* Chart */}
      <div className="w-full">
        {data.length === 0 ? (
          <EmptyState themeMuted={theme.textMuted} />
        ) : (
          <svg
            viewBox={`0 0 640 ${height}`}
            role="img"
            aria-label="Γ (line), EMA(Γ) (line), Θ (guide)"
            style={{ width: "100%", height }}
          >
            {/* grid */}
            {yTicks.map((t, i) => (
              <g key={`g-${i}`}>
                <line
                  x1={0}
                  x2={640}
                  y1={t.y}
                  y2={t.y}
                  stroke={theme.grid}
                  strokeWidth={1}
                  strokeDasharray="2 3"
                />
                <text
                  x={4}
                  y={t.y - 2}
                  fontSize={10}
                  fill={theme.textMuted}
                  className="tabular-nums"
                >
                  {yFmt(t.v)}
                </text>
              </g>
            ))}

            {/* Theta guideline */}
            <line
              x1={0}
              x2={640}
              y1={yTheta}
              y2={yTheta}
              stroke={theme.textMuted}
              strokeWidth={1.5}
              strokeDasharray="4 4"
            />
            <text
              x={636}
              y={yTheta - 4}
              fontSize={10}
              textAnchor="end"
              fill={theme.textMuted}
            >
              Θ
            </text>

            {/* EMA line */}
            <path
              d={pathForLine(xs, yEma)}
              fill="none"
              stroke={theme.accentAlt ?? theme.textPrimary}
              strokeWidth={2}
            />

            {/* Gamma line */}
            <path
              d={pathForLine(xs, yGamma)}
              fill="none"
              stroke={theme.accent ?? theme.textEmphasis}
              strokeWidth={1.5}
              opacity={0.9}
            />
          </svg>
        )}
      </div>

      {/* Delta vs Θ */}
      <footer className="flex flex-wrap items-center gap-4">
        <DeltaBadge
          label="EMA vs Θ"
          ratio={ratio}
          deltaPct={deltaPct}
          theme={{
            pos: "#14866d",
            neg: "#b54747",
            text: theme.textPrimary,
            muted: theme.textMuted,
            bg: theme.surfaceAlt,
            border: theme.grid,
          }}
        />
        <p className="text-xs" style={{ color: theme.textMuted }}>
          If EMA(Γ) &gt; Θ, demand exceeds target capacity; if EMA(Γ) &lt; Θ, the system is
          under target. Adjust Θ or fees to steer utilization.
        </p>
      </footer>
    </section>
  );
};

/* ------------------------------- subcomponents ---------------------------- */

const Stat: React.FC<{ label: string; value: string | number; themeColor: string }> = ({
  label,
  value,
  themeColor,
}) => {
  return (
    <div className="space-y-0.5">
      <div className="text-xs" style={{ color: themeColor, opacity: 0.7 }}>
        {label}
      </div>
      <div className="text-base font-semibold tabular-nums" style={{ color: themeColor }}>
        {value}
      </div>
    </div>
  );
};

const EmptyState: React.FC<{ themeMuted: string }> = ({ themeMuted }) => (
  <div className="h-[140px] grid place-items-center text-sm" style={{ color: themeMuted }}>
    No data to display
  </div>
);

const DeltaBadge: React.FC<{
  label: string;
  ratio?: number;
  deltaPct?: number;
  theme: { pos: string; neg: string; text: string; muted: string; bg: string; border: string };
}> = ({ label, ratio, deltaPct, theme }) => {
  const up = (deltaPct ?? 0) > 0;
  const color = up ? theme.pos : theme.neg;
  return (
    <div
      className="rounded-md border px-2 py-1 flex items-center gap-2"
      style={{ background: theme.bg, borderColor: theme.border }}
      aria-live="polite"
    >
      <span className="text-xs" style={{ color: theme.muted }}>
        {label}
      </span>
      <strong className="text-sm tabular-nums" style={{ color }}>
        {ratio !== undefined ? FORMATS_DEFAULT.number(ratio) + "×" : "—"}
      </strong>
      <span className="text-xs tabular-nums" style={{ color }}>
        {deltaPct !== undefined ? FORMATS_DEFAULT.signPercent(deltaPct) : ""}
      </span>
    </div>
  );
};

export default GammaPanel;
