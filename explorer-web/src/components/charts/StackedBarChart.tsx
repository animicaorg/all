import React, { useEffect, useMemo, useRef, useState } from "react";
import cn from "../../utils/classnames";

/**
 * Generic stacked bar chart for "proof mix per block" (or any categorical stack).
 * - Responsive (ResizeObserver)
 * - Legend with show/hide toggles
 * - Optional 100% normalization
 * - SVG-only, zero dependencies
 */

type Num = number;

export type Category = {
  id: string;        // key in datum.values
  name?: string;     // display name
  color?: string;    // CSS color (or var)
  hidden?: boolean;
  order?: number;    // lower first (default: index order)
};

export type StackedDatum = {
  x: Num | string;                    // e.g. block height
  values: Record<string, number>;     // { categoryId: value }
};

export interface StackedBarChartProps {
  data: StackedDatum[];
  categories: Category[];

  height?: number;
  className?: string;

  /** If true, bars are normalized to 100% (default). */
  normalized?: boolean;

  /** Optional y-domain override (only used when normalized=false). */
  yDomain?: [number, number];

  /** Show grid lines and axes labels */
  grid?: boolean;
  legend?: boolean;

  /** Formatting */
  xLabel?: string;
  yLabel?: string;
  xFormat?: (x: Num | string) => string;
  yFormat?: (y: number) => string;

  /** Visuals */
  barWidthRatio?: number; // 0..1 (default .72)
  roundRadius?: number;   // bar corner radius (default 2)

  /** Events */
  selectedX?: Num | string;
  onSelectBar?: (x: Num | string) => void;
}

const DEFAULT_HEIGHT = 220;
const MARGINS = { top: 12, right: 12, bottom: 30, left: 52 };
const PALETTE = [
  "var(--chart-a, #4F46E5)",
  "var(--chart-b, #22C55E)",
  "var(--chart-c, #F59E0B)",
  "var(--chart-d, #EC4899)",
  "var(--chart-e, #06B6D4)",
  "var(--chart-f, #A855F7)",
  "var(--chart-g, #84CC16)",
  "var(--chart-h, #F97316)",
];

type PreparedCategory = Required<Pick<Category, "id">> & Category & { color: string };

type BarStack = {
  x: Num | string;
  total: number;
  segments: {
    id: string;
    y0: number; // lower bound in chart units (normalized 0..1 or absolute)
    y1: number; // upper bound
    value: number;
    color: string;
  }[];
};

export default function StackedBarChart({
  data,
  categories,
  height = DEFAULT_HEIGHT,
  className,
  normalized = true,
  yDomain,
  grid = true,
  legend = true,
  xLabel,
  yLabel,
  xFormat,
  yFormat,
  barWidthRatio = 0.72,
  roundRadius = 2,
  selectedX,
  onSelectBar,
}: StackedBarChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<Num>(640);
  // internal visibility state (legend toggles)
  const [hidden, setHidden] = useState<Record<string, boolean>>(
    () => Object.fromEntries(categories.map((c) => [c.id, !!c.hidden]))
  );
  // hover (nearest bar index)
  const [hover, setHover] = useState<{ i: number } | null>(null);

  // Resize observer
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r && r.width > 0) setWidth(Math.floor(r.width));
    });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  // Prepare categories with palette + sort order
  const cats: PreparedCategory[] = useMemo(() => {
    const withColors = categories.map((c, i) => ({
      ...c,
      color: c.color || PALETTE[i % PALETTE.length],
    })) as PreparedCategory[];
    return withColors
      .slice()
      .sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }, [categories]);

  // Compute stacks
  const prepared = useMemo(() => {
    const visibleCats = cats.filter((c) => !hidden[c.id]);
    const bars: BarStack[] = data.map((d) => {
      const total = visibleCats.reduce((acc, c) => acc + (d.values[c.id] ?? 0), 0);
      let yAcc = 0;
      const segs = visibleCats.map((c) => {
        const v = d.values[c.id] ?? 0;
        const y0 = normalized ? (total ? yAcc / total : 0) : yAcc;
        yAcc += v;
        const y1 = normalized ? (total ? yAcc / total : 0) : yAcc;
        return { id: c.id, y0, y1, value: v, color: c.color };
      });
      return { x: d.x, total, segments: segs };
    });

    const innerW = Math.max(0, width - MARGINS.left - MARGINS.right);
    const innerH = Math.max(0, height - MARGINS.top - MARGINS.bottom);

    const yMin = 0;
    const yMax = normalized
      ? 1
      : yDomain
      ? yDomain[1]
      : Math.max(1, ...bars.map((b) => b.segments.at(-1)?.y1 ?? 0));

    const yTicks = normalized ? [0, 0.25, 0.5, 0.75, 1] : niceTicks(yMin, yMax, 5);
    const yFmt =
      yFormat ||
      (normalized
        ? (v: number) => (v * 100).toFixed(0) + "%"
        : (v: number) => formatNumber(v));

    const xFmt =
      xFormat ||
      ((v: Num | string) => {
        if (typeof v === "number") return String(v);
        return v;
      });

    return { bars, innerW, innerH, yMin, yMax, yTicks, yFmt, xFmt, visibleCats };
  }, [data, cats, hidden, width, height, normalized, yDomain, yFormat, xFormat]);

  const { innerW, innerH } = prepared;
  const n = Math.max(1, prepared.bars.length);
  const step = innerW / n;
  const bw = Math.max(2, Math.min(step, step * barWidthRatio));

  // scales
  const sxIdx = (i: number) => MARGINS.left + i * step + step / 2;
  const sy = (y: number) =>
    MARGINS.top + (1 - (y - 0) / Math.max(1e-12, prepared.yMax - 0)) * innerH;

  const xTicksIdx = chooseXTicks(prepared.bars.length, 8);

  // hover / click
  function handleMove(evt: React.MouseEvent<SVGSVGElement, MouseEvent>) {
    const rect = (evt.currentTarget as SVGSVGElement).getBoundingClientRect();
    const px = evt.clientX - rect.left - MARGINS.left;
    const i = Math.round(px / step - 0.5);
    if (i >= 0 && i < prepared.bars.length) setHover({ i });
    else setHover(null);
  }
  function handleLeave() {
    setHover(null);
  }
  function handleClick() {
    if (!hover) return;
    onSelectBar?.(prepared.bars[hover.i].x);
  }

  return (
    <div ref={wrapRef} className={cn("ow-chart-card", className)}>
      {legend && prepared.visibleCats.length > 0 && (
        <div className="ow-legend" role="list">
          {cats.map((c, i) => (
            <button
              key={c.id}
              role="listitem"
              type="button"
              className={cn("ow-legend-item", hidden[c.id] && "is-hidden")}
              onClick={() => setHidden((h) => ({ ...h, [c.id]: !h[c.id] }))}
              title={hidden[c.id] ? "Show" : "Hide"}
            >
              <span
                className="ow-legend-dot"
                style={{
                  // @ts-expect-error custom var ok
                  "--dot": c.color || PALETTE[i % PALETTE.length],
                }}
              />
              <span className="ow-legend-label">{c.name || c.id}</span>
            </button>
          ))}
        </div>
      )}

      <svg
        width={width}
        height={height}
        className="ow-chart-svg"
        onMouseMove={handleMove}
        onMouseLeave={handleLeave}
        onClick={handleClick}
        role="img"
        aria-label="Stacked bar chart"
      >
        {/* Grid */}
        {grid && (
          <g className="ow-grid">
            {prepared.yTicks.map((t, k) => (
              <line
                key={`gy-${k}-${t}`}
                x1={MARGINS.left}
                x2={MARGINS.left + innerW}
                y1={sy(t)}
                y2={sy(t)}
              />
            ))}
          </g>
        )}

        {/* Bars */}
        <g className="ow-series">
          {prepared.bars.length === 0 && (
            <text
              x={MARGINS.left + innerW / 2}
              y={MARGINS.top + innerH / 2}
              textAnchor="middle"
              className="ow-empty"
            >
              No data
            </text>
          )}

          {prepared.bars.map((b, i) => {
            const cx = sxIdx(i);
            const x = cx - bw / 2;
            const isSelected =
              selectedX !== undefined && equalsX(selectedX, b.x);
            const isHover = hover?.i === i;

            return (
              <g key={`bar-${i}`} aria-label={`bar-${i}`}>
                {b.segments.map((s) => {
                  const y0 = sy(s.y0);
                  const y1 = sy(s.y1);
                  const h = Math.max(0, y1 - y0);
                  // SVG rect y grows downward, so use y0
                  return (
                    <rect
                      key={s.id}
                      x={x}
                      y={y0}
                      width={bw}
                      height={h}
                      rx={roundRadius}
                      fill={s.color}
                      opacity={hidden[s.id] ? 0.15 : 1}
                    />
                  );
                })}
                {/* hover/selection outline */}
                {(isHover || isSelected) && (
                  <rect
                    x={x - 1}
                    y={MARGINS.top}
                    width={bw + 2}
                    height={innerH}
                    fill="none"
                    stroke={isSelected ? "var(--accent, #22C55E)" : "rgba(255,255,255,.25)"}
                    strokeWidth={isSelected ? 2 : 1}
                    pointerEvents="none"
                  />
                )}
              </g>
            );
          })}
        </g>

        {/* Axes */}
        <g className="ow-axis">
          {/* Y axis */}
          <line
            x1={MARGINS.left}
            x2={MARGINS.left}
            y1={MARGINS.top}
            y2={MARGINS.top + innerH}
          />
          {prepared.yTicks.map((t, k) => (
            <g key={`yt-${k}`} transform={`translate(${MARGINS.left},${sy(t)})`}>
              <line x1="-6" />
              <text x="-10" textAnchor="end" dy="0.32em">
                {prepared.yFmt(t)}
              </text>
            </g>
          ))}
          {yLabel && (
            <text
              x={-MARGINS.top - innerH / 2}
              y={12}
              transform="rotate(-90)"
              textAnchor="middle"
              className="ow-axis-label"
            >
              {yLabel}
            </text>
          )}

          {/* X axis */}
          <line
            x1={MARGINS.left}
            x2={MARGINS.left + innerW}
            y1={MARGINS.top + innerH}
            y2={MARGINS.top + innerH}
          />
          {xTicksIdx.map((i) => {
            const cx = sxIdx(i);
            const label = prepared.xFmt(prepared.bars[i].x);
            return (
              <g key={`xt-${i}`} transform={`translate(${cx},${MARGINS.top + innerH})`}>
                <line y2="6" />
                <text y="18" textAnchor="middle">
                  {label}
                </text>
              </g>
            );
          })}
          {xLabel && (
            <text
              x={MARGINS.left + innerW / 2}
              y={height - 2}
              textAnchor="middle"
              className="ow-axis-label"
            >
              {xLabel}
            </text>
          )}
        </g>
      </svg>

      {/* Tooltip */}
      {hover && prepared.bars[hover.i] && (
        <BarTooltip
          left={sxIdx(hover.i) + 8}
          top={MARGINS.top + 8}
          bar={prepared.bars[hover.i]}
          cats={cats}
          xFmt={prepared.xFmt}
          yFmt={prepared.yFmt}
          normalized={normalized}
        />
      )}

      <style>{styles}</style>
    </div>
  );
}

/* -------------------------------- Tooltip ---------------------------------- */

function BarTooltip({
  left,
  top,
  bar,
  cats,
  xFmt,
  yFmt,
  normalized,
}: {
  left: number;
  top: number;
  bar: BarStack;
  cats: PreparedCategory[];
  xFmt: (x: Num | string) => string;
  yFmt: (y: number) => string;
  normalized: boolean;
}) {
  const catById = Object.fromEntries(cats.map((c) => [c.id, c]));
  const title = `Block ${xFmt(bar.x)}`;
  const totalFmt = normalized ? "" : `Total: ${yFmt(bar.total)}`;

  return (
    <div className="ow-tooltip" style={{ left, top }} role="tooltip">
      <div className="ow-tooltip-title">{title}</div>
      {totalFmt && <div className="ow-tooltip-sub">{totalFmt}</div>}

      <div className="ow-tooltip-list">
        {bar.segments
          .slice()
          .reverse() // top first
          .map((s) => {
            const c = catById[s.id];
            const name = c?.name || s.id;
            const shown = normalized ? (s.y1 - s.y0) : s.value;
            const val = normalized ? ((s.y1 - s.y0) * 100).toFixed(0) + "%" : yFmt(s.value);
            return (
              <div key={s.id} className="ow-tooltip-row">
                <span
                  className="ow-dot"
                  style={{
                    // @ts-expect-error CSS var ok
                    "--dot": c?.color || s.color,
                  }}
                />
                <span className="ow-tooltip-text">
                  {name}: <strong>{val}</strong>
                </span>
              </div>
            );
          })}
      </div>
    </div>
  );
}

/* ------------------------------- Utilities --------------------------------- */

function chooseXTicks(n: number, maxTicks: number): number[] {
  if (n <= maxTicks) return Array.from({ length: n }, (_, i) => i);
  const step = Math.max(1, Math.floor(n / maxTicks));
  const ticks: number[] = [];
  for (let i = 0; i < n; i += step) ticks.push(i);
  if (ticks[ticks.length - 1] !== n - 1) ticks.push(n - 1);
  return ticks;
}

function equalsX(a: Num | string, b: Num | string): boolean {
  return a === b;
}

function niceTicks(min: number, max: number, count: number): number[] {
  if (!isFinite(min) || !isFinite(max) || min === max) return [min || 0];
  const span = max - min;
  const step0 = Math.pow(10, Math.floor(Math.log10(span / Math.max(1, count))));
  const err = (count * step0) / span;
  const step =
    err <= 0.15 ? step0 * 10 : err <= 0.35 ? step0 * 5 : err <= 0.75 ? step0 * 2 : step0;
  const t0 = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let v = t0; v <= max + 1e-9; v += step) ticks.push(+v.toFixed(12));
  return ticks;
}

function formatNumber(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2).replace(/\.00$/, "") + "B";
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(2).replace(/\.00$/, "") + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(2).replace(/\.00$/, "") + "k";
  if (a >= 1) return n.toFixed(2).replace(/\.00$/, "");
  if (a === 0) return "0";
  return n.toPrecision(3);
}

/* --------------------------------- Styles ---------------------------------- */

const styles = `
.ow-chart-card {
  position: relative;
  border: 1px solid var(--border, rgba(255,255,255,.12));
  border-radius: 12px;
  background: var(--panel, #0b1020);
  padding: 8px 8px 4px 8px;
}

.ow-legend {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px 12px;
  padding: 4px 6px 6px 6px;
}
.ow-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 4px 8px;
  border-radius: 8px;
  border: 1px solid var(--border, rgba(255,255,255,.12));
  background: transparent;
  color: var(--fg, #e5e7eb);
  cursor: pointer;
  font-size: 12px;
}
.ow-legend-item.is-hidden { opacity: .5; }
.ow-legend-item:hover { background: rgba(255,255,255,.05); }
.ow-legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--dot, #6b7280);
}

.ow-chart-svg { display: block; width: 100%; height: auto; }
.ow-grid line {
  stroke: rgba(255,255,255,.08);
  shape-rendering: crispEdges;
}
.ow-axis line { stroke: rgba(255,255,255,.18); shape-rendering: crispEdges; }
.ow-axis text {
  fill: var(--muted, #9aa4b2);
  font-size: 12px;
}
.ow-axis-label {
  fill: var(--muted, #9aa4b2);
  font-size: 12px;
}

.ow-series rect { mix-blend-mode: normal; }
.ow-empty { fill: var(--muted, #9aa4b2); }

.ow-tooltip {
  position: absolute;
  transform: translateY(-100%);
  min-width: 160px;
  max-width: 280px;
  background: rgba(17,24,39,.95);
  color: #e5e7eb;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 8px;
  padding: 8px 10px;
  pointer-events: none;
  box-shadow: 0 8px 20px rgba(0,0,0,.35);
}
.ow-tooltip-title { font-weight: 600; font-size: 12px; margin-bottom: 4px; }
.ow-tooltip-sub { font-size: 11px; color: #9aa4b2; margin-bottom: 6px; }
.ow-tooltip-list { display: flex; flex-direction: column; gap: 4px; }
.ow-tooltip-row { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.ow-tooltip .ow-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--dot, #6b7280);
}
`;

