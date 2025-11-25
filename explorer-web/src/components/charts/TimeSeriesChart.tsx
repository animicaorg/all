import React, { useEffect, useMemo, useRef, useState } from "react";
import cn from "../../utils/classnames";

type Num = number;
export type Point = { x: Num | Date; y: Num | null };
export type Series = {
  id: string;
  name?: string;
  data: Point[];
  color?: string;       // CSS color or CSS var
  dashed?: boolean;
  hidden?: boolean;
};

export interface TimeSeriesChartProps {
  series: Series[];
  height?: number;
  className?: string;

  // Domains
  xDomain?: "auto" | [Num, Num];
  yDomain?: "auto" | [Num, Num];

  // Presentation
  grid?: boolean;
  smooth?: boolean; // currently draws straight lines; reserved for future
  downsample?: "auto" | number; // max points per series after LTTB
  xLabel?: string;
  yLabel?: string;
  legend?: boolean;

  // Formatting
  yFormat?: (y: number) => string;
  xFormat?: (x: number) => string;
  tooltipFormat?: (x: number, y: number, s: Series) => string;

  // Events
  onRangeChange?: (xMin: number, xMax: number) => void;
}

const DEFAULT_HEIGHT = 220;
const MARGINS = { top: 12, right: 16, bottom: 28, left: 48 };
const PALETTE = [
  "var(--chart-a, #4F46E5)",
  "var(--chart-b, #22C55E)",
  "var(--chart-c, #F59E0B)",
  "var(--chart-d, #EC4899)",
  "var(--chart-e, #06B6D4)",
  "var(--chart-f, #A855F7)",
];

export default function TimeSeriesChart({
  series,
  height = DEFAULT_HEIGHT,
  className,
  xDomain = "auto",
  yDomain = "auto",
  grid = true,
  smooth = false, // reserved
  downsample = "auto",
  xLabel,
  yLabel,
  legend = true,
  yFormat = (y) => formatNumber(y),
  xFormat,
  tooltipFormat,
  onRangeChange,
}: TimeSeriesChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<Num>(640);
  const [hidden, setHidden] = useState<Record<string, boolean>>(
    () => Object.fromEntries(series.map((s) => [s.id, !!s.hidden]))
  );
  const [hover, setHover] = useState<{ x: number; y: number; sx: number; sy: number; sid: string } | null>(null);

  // Resize observer
  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const e = entries[0];
      if (e && e.contentRect.width > 0) {
        setWidth(Math.floor(e.contentRect.width));
      }
    });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  // Prepare data
  const prepared = useMemo(() => {
    const filtered = series
      .map((s, i) => ({
        ...s,
        color: s.color || PALETTE[i % PALETTE.length],
        data: s.data
          .map((p) => ({ x: toMs(p.x), y: p.y }))
          .filter((p) => p.y != null && Number.isFinite(p.y as number)) as { x: number; y: number }[],
      }))
      .filter((s) => s.data.length > 0);

    // Downsample each series if needed
    const maxPts =
      downsample === "auto"
        ? Math.max(200, Math.min(1000, Math.floor((width - MARGINS.left - MARGINS.right) * 0.75)))
        : typeof downsample === "number"
        ? Math.max(50, downsample)
        : Infinity;

    const ds = filtered.map((s) => ({
      ...s,
      data: s.data.length > maxPts ? lttb(s.data, maxPts) : s.data,
    }));

    // Compute domains
    const xMin = xDomain === "auto" ? Math.min(...ds.map((s) => s.data[0].x)) : xDomain[0];
    const xMax =
      xDomain === "auto" ? Math.max(...ds.map((s) => s.data[s.data.length - 1].x)) : xDomain[1];
    const yMin =
      yDomain === "auto"
        ? Math.min(...ds.map((s) => Math.min(...s.data.map((p) => p.y))))
        : yDomain[0];
    const yMax =
      yDomain === "auto"
        ? Math.max(...ds.map((s) => Math.max(...s.data.map((p) => p.y))))
        : yDomain[1];

    const xFmt =
      xFormat ||
      ((x: number) => {
        return formatTimeAxis(xMin, xMax, x);
      });

    return { series: ds, xMin, xMax, yMin, yMax, xFmt };
  }, [series, xDomain, yDomain, downsample, width, xFormat]);

  const { innerW, innerH } = {
    innerW: Math.max(0, width - MARGINS.left - MARGINS.right),
    innerH: Math.max(0, height - MARGINS.top - MARGINS.bottom),
  };

  // Scales
  const sx = (x: number) => {
    if (prepared.xMax === prepared.xMin) return MARGINS.left + innerW / 2;
    return MARGINS.left + ((x - prepared.xMin) / (prepared.xMax - prepared.xMin)) * innerW;
  };
  const sy = (y: number) => {
    if (prepared.yMax === prepared.yMin) return MARGINS.top + innerH / 2;
    return MARGINS.top + (1 - (y - prepared.yMin) / (prepared.yMax - prepared.yMin)) * innerH;
  };

  // Axes ticks
  const xTicks = useMemo(
    () => timeTicks(prepared.xMin, prepared.xMax, 6),
    [prepared.xMin, prepared.xMax]
  );
  const yTicks = useMemo(
    () => niceTicks(prepared.yMin, prepared.yMax, 5),
    [prepared.yMin, prepared.yMax]
  );

  // Hover handling
  function handleMove(evt: React.MouseEvent<SVGSVGElement, MouseEvent>) {
    const rect = (evt.target as Element).closest("svg")!.getBoundingClientRect();
    const px = evt.clientX - rect.left;
    const py = evt.clientY - rect.top;

    const xVal =
      prepared.xMin +
      ((px - MARGINS.left) / Math.max(1, innerW)) * (prepared.xMax - prepared.xMin);

    let best:
      | { dist: number; x: number; y: number; sx: number; sy: number; sid: string }
      | null = null;

    for (const s of prepared.series) {
      if (hidden[s.id]) continue;
      const idx = bisectX(s.data, xVal);
      const cand = [s.data[idx - 1], s.data[idx], s.data[idx + 1]].filter(Boolean);
      for (const p of cand) {
        const dx = Math.abs(sx(p.x) - px);
        const dy = Math.abs(sy(p.y) - py);
        const d = Math.hypot(dx, dy);
        if (!best || d < best.dist) {
          best = { dist: d, x: p.x, y: p.y, sx: sx(p.x), sy: sy(p.y), sid: s.id };
        }
      }
    }
    setHover(best && best.dist < 40 ? best : null);
  }

  function handleLeave() {
    setHover(null);
  }

  // Legend toggles
  const visibleSeries = prepared.series.filter((s) => !hidden[s.id]);

  return (
    <div ref={wrapRef} className={cn("ow-chart-card", className)}>
      {legend && prepared.series.length > 0 && (
        <div className="ow-legend" role="list">
          {prepared.series.map((s, i) => (
            <button
              key={s.id}
              role="listitem"
              type="button"
              className={cn("ow-legend-item", hidden[s.id] && "is-hidden")}
              onClick={() => setHidden((h) => ({ ...h, [s.id]: !h[s.id] }))}
              title={hidden[s.id] ? "Show series" : "Hide series"}
            >
              <span
                className="ow-legend-dot"
                style={{
                  // @ts-expect-error CSSVar ok
                  "--dot": s.color || PALETTE[i % PALETTE.length],
                }}
              />
              <span className="ow-legend-label">{s.name || s.id}</span>
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
        role="img"
        aria-label="Time series chart"
      >
        {/* Grid */}
        {grid && (
          <g className="ow-grid">
            {/* vertical */}
            {xTicks.map((t) => (
              <line
                key={`gx-${t}`}
                x1={sx(t)}
                x2={sx(t)}
                y1={MARGINS.top}
                y2={MARGINS.top + innerH}
              />
            ))}
            {/* horizontal */}
            {yTicks.map((t) => (
              <line
                key={`gy-${t}`}
                x1={MARGINS.left}
                x2={MARGINS.left + innerW}
                y1={sy(t)}
                y2={sy(t)}
              />
            ))}
          </g>
        )}

        {/* Series paths */}
        <g className="ow-series">
          {visibleSeries.length === 0 && (
            <text
              x={MARGINS.left + innerW / 2}
              y={MARGINS.top + innerH / 2}
              textAnchor="middle"
              className="ow-empty"
            >
              No data
            </text>
          )}
          {visibleSeries.map((s, i) => {
            const d = pathFor(s.data, sx, sy, smooth);
            return (
              <path
                key={s.id}
                d={d}
                fill="none"
                stroke={s.color || PALETTE[i % PALETTE.length]}
                strokeWidth={2}
                strokeDasharray={s.dashed ? "5 5" : undefined}
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
        </g>

        {/* Axes */}
        <g className="ow-axis">
          {/* X axis */}
          <line
            x1={MARGINS.left}
            x2={MARGINS.left + innerW}
            y1={MARGINS.top + innerH}
            y2={MARGINS.top + innerH}
          />
          {xTicks.map((t) => (
            <g key={`xt-${t}`} transform={`translate(${sx(t)},${MARGINS.top + innerH})`}>
              <line y2="6" />
              <text y="20" textAnchor="middle">
                {prepared.xFmt(t)}
              </text>
            </g>
          ))}
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

          {/* Y axis */}
          <line
            x1={MARGINS.left}
            x2={MARGINS.left}
            y1={MARGINS.top}
            y2={MARGINS.top + innerH}
          />
          {yTicks.map((t) => (
            <g key={`yt-${t}`} transform={`translate(${MARGINS.left},${sy(t)})`}>
              <line x1="-6" />
              <text x="-10" textAnchor="end" dy="0.32em">
                {yFormat(t)}
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
        </g>

        {/* Crosshair & tooltip */}
        {hover && (
          <>
            <line
              className="ow-crosshair"
              x1={hover.sx}
              x2={hover.sx}
              y1={MARGINS.top}
              y2={MARGINS.top + innerH}
            />
            <circle className="ow-hit" cx={hover.sx} cy={hover.sy} r={3.5} />
          </>
        )}
      </svg>

      {/* Tooltip (HTML for better text wrap) */}
      {hover && (
        <ChartTooltip
          x={hover.x}
          y={hover.y}
          sid={hover.sid}
          left={hover.sx + 8}
          top={MARGINS.top + 8}
          series={prepared.series}
          yFormat={yFormat}
          xLabel={xLabel}
          xFmt={prepared.xFmt}
          tooltipFormat={tooltipFormat}
        />
      )}

      <style>{styles}</style>
    </div>
  );
}

/* ------------------------------- Tooltip ----------------------------------- */

function ChartTooltip({
  x,
  y,
  sid,
  series,
  left,
  top,
  yFormat,
  xLabel,
  xFmt,
  tooltipFormat,
}: {
  x: number;
  y: number;
  sid: string;
  series: Series[];
  left: number;
  top: number;
  yFormat: (y: number) => string;
  xLabel?: string;
  xFmt: (x: number) => string;
  tooltipFormat?: (x: number, y: number, s: Series) => string;
}) {
  const s = series.find((s) => s.id === sid)!;
  const label = tooltipFormat
    ? tooltipFormat(x, y, s)
    : `${s.name || s.id}: ${yFormat(y)}${xLabel ? ` @ ${xFmt(x)}` : ""}`;

  return (
    <div
      className="ow-tooltip"
      style={{ left, top }}
      role="tooltip"
      aria-live="polite"
      data-testid="chart-tooltip"
    >
      <div className="ow-tooltip-row">
        <span
          className="ow-dot"
          style={{
            // @ts-expect-error css var ok
            "--dot": s.color || PALETTE[0],
          }}
        />
        <span className="ow-tooltip-text">{label}</span>
      </div>
      <div className="ow-tooltip-sub">{xFmt(x)}</div>
    </div>
  );
}

/* ------------------------------ Utilities ---------------------------------- */

function toMs(x: number | Date): number {
  if (x instanceof Date) return x.getTime();
  return x;
}

function formatNumber(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2).replace(/\.00$/, "") + "B";
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(2).replace(/\.00$/, "") + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(2).replace(/\.00$/, "") + "k";
  if (a >= 1) return n.toFixed(2).replace(/\.00$/, "");
  if (a === 0) return "0";
  // small values
  const s = n.toPrecision(3);
  return s;
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

function timeTicks(min: number, max: number, count: number): number[] {
  const span = Math.max(1, max - min);
  const sec = span / 1000;
  const candidates = [
    1000, // 1s
    2000,
    5000,
    10_000,
    15_000,
    30_000,
    60_000, // 1m
    120_000,
    300_000,
    600_000,
    900_000,
    1_800_000,
    3_600_000, // 1h
    7_200_000,
    14_400_000,
    21_600_000,
    43_200_000,
    86_400_000, // 1d
  ];
  let step = candidates[0];
  for (const c of candidates) {
    if (span / c <= count) {
      step = c;
      break;
    }
    step = c;
  }
  const t0 = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let t = t0; t <= max; t += step) ticks.push(t);
  return ticks;
}

function two(n: number) {
  return String(n).padStart(2, "0");
}

function formatTimeAxis(min: number, max: number, x: number): string {
  const span = max - min;
  const d = new Date(x);
  if (span < 2 * 60_000) {
    // < 2 min
    return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
  } else if (span < 2 * 86_400_000) {
    // < 2 days
    return `${two(d.getHours())}:${two(d.getMinutes())}`;
  } else {
    return `${d.getFullYear()}-${two(d.getMonth() + 1)}-${two(d.getDate())}`;
  }
}

function pathFor(
  pts: { x: number; y: number }[],
  sx: (x: number) => number,
  sy: (y: number) => number,
  _smooth: boolean
): string {
  if (pts.length === 0) return "";
  let d = `M ${sx(pts[0].x)} ${sy(pts[0].y)}`;
  for (let i = 1; i < pts.length; i++) {
    d += ` L ${sx(pts[i].x)} ${sy(pts[i].y)}`;
  }
  return d;
}

function bisectX(a: { x: number; y: number }[], x: number): number {
  let lo = 0,
    hi = a.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    if (a[mid].x < x) lo = mid + 1;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * LTTB downsampling (Largest-Triangle-Three-Buckets)
 * Returns subset of points preserving visual characteristics.
 */
function lttb(data: { x: number; y: number }[], threshold: number) {
  if (threshold >= data.length || threshold === 0) return data.slice();
  const sampled: { x: number; y: number }[] = [];
  let bucketSize = (data.length - 2) / (threshold - 2);
  let a = 0; // first point is always included
  sampled.push(data[a]);

  for (let i = 0; i < threshold - 2; i++) {
    const start = Math.floor((i + 1) * bucketSize) + 1;
    const end = Math.floor((i + 2) * bucketSize) + 1;
    const range = data.slice(start, Math.min(end, data.length));

    // avg for next bucket
    let avgX = 0,
      avgY = 0;
    const avgRangeStart = Math.floor((i + 1) * bucketSize) + 1;
    const avgRangeEnd = Math.floor((i + 2) * bucketSize) + 1;
    const avgRange = data.slice(avgRangeStart, Math.min(avgRangeEnd, data.length));
    for (const p of avgRange) {
      avgX += p.x;
      avgY += p.y;
    }
    avgX /= avgRange.length || 1;
    avgY /= avgRange.length || 1;

    // point with max triangle area
    let maxArea = -1;
    let maxAreaPoint = range[0] || data[data.length - 2];
    for (const p of range) {
      const area = Math.abs(
        (data[a].x - avgX) * (p.y - data[a].y) - (data[a].x - p.x) * (avgY - data[a].y)
      );
      if (area > maxArea) {
        maxArea = area;
        maxAreaPoint = p;
      }
    }
    sampled.push(maxAreaPoint);
    a = data.indexOf(maxAreaPoint);
  }
  sampled.push(data[data.length - 1]); // last point
  return sampled;
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
.ow-series path { mix-blend-mode: normal; }
.ow-axis line { stroke: rgba(255,255,255,.18); shape-rendering: crispEdges; }
.ow-axis text {
  fill: var(--muted, #9aa4b2);
  font-size: 12px;
}
.ow-axis-label {
  fill: var(--muted, #9aa4b2);
  font-size: 12px;
}
.ow-crosshair {
  stroke: rgba(255,255,255,.25);
  shape-rendering: crispEdges;
}
.ow-hit {
  fill: var(--fg, #e5e7eb);
  stroke: #000;
  stroke-width: 1;
}

.ow-empty {
  fill: var(--muted, #9aa4b2);
}

.ow-tooltip {
  position: absolute;
  transform: translateY(-100%);
  min-width: 140px;
  max-width: 260px;
  background: rgba(17,24,39,.95);
  color: #e5e7eb;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 8px;
  padding: 8px 10px;
  pointer-events: none;
  box-shadow: 0 8px 20px rgba(0,0,0,.35);
}
.ow-tooltip-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.ow-tooltip .ow-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--dot, #6b7280);
}
.ow-tooltip-text { font-size: 12px; }
.ow-tooltip-sub {
  margin-top: 4px;
  font-size: 11px;
  color: #9aa4b2;
}
`;

