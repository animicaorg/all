import React, { useEffect, useMemo, useRef, useState } from "react";
import cn from "../../utils/classnames";

/**
 * DonutChart — for fairness share / provider stakes visualizations.
 * - Pure SVG, no deps
 * - Responsive (ResizeObserver)
 * - Legend with show/hide toggles
 * - Center summary (auto-switches to hovered/selected slice)
 * - Accessible (role="img", titles)
 */

type Num = number;

export type DonutSlice = {
  id: string;
  name?: string;
  value: number;
  color?: string;
  hidden?: boolean;
  order?: number; // lower comes first around the circle
};

export interface DonutChartProps {
  data: DonutSlice[];

  /** Component sizing */
  height?: number;            // outer SVG height (responsive width)
  className?: string;

  /** Visual tuning */
  innerRadiusRatio?: number;  // 0..1, default .62
  padAngleDeg?: number;       // gap between slices in degrees, default 1.5
  roundedCaps?: boolean;      // rounded stroke caps, default true
  startAngleDeg?: number;     // rotation, default -90 (12 o'clock)

  /** Legend / labels */
  legend?: boolean;
  showPercent?: boolean;      // tooltip/legend formatting
  decimals?: number;          // percent/value decimals, default 1

  /** Selection / events */
  selectedId?: string;
  onSelect?: (id: string | null) => void;

  /** Center summary */
  centerTitle?: string;       // e.g. "Fairness"
  centerFormatter?: (value: number) => string;

  /** Sorting: by 'asc' | 'desc' | 'none' (default: order then input index) */
  sort?: "asc" | "desc" | "none";
}

const DEFAULT_HEIGHT = 240;
const MARGINS = { top: 8, right: 8, bottom: 8, left: 8 };
const PALETTE = [
  "var(--chart-a, #4F46E5)",
  "var(--chart-b, #22C55E)",
  "var(--chart-c, #F59E0B)",
  "var(--chart-d, #EC4899)",
  "var(--chart-e, #06B6D4)",
  "var(--chart-f, #A855F7)",
  "var(--chart-g, #84CC16)",
  "var(--chart-h, #F97316)",
  "var(--chart-i, #10B981)",
  "var(--chart-j, #60A5FA)",
];

export default function DonutChart({
  data,
  height = DEFAULT_HEIGHT,
  className,
  innerRadiusRatio = 0.62,
  padAngleDeg = 1.5,
  roundedCaps = true,
  startAngleDeg = -90,
  legend = true,
  showPercent = true,
  decimals = 1,
  selectedId,
  onSelect,
  centerTitle = "Total",
  centerFormatter,
  sort = "none",
}: DonutChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const svgRef = useRef<SVGSVGElement | null>(null);
  const [width, setWidth] = useState<Num>(480);
  const [hoverId, setHoverId] = useState<string | null>(null);
  const [tooltip, setTooltip] = useState<{ left: number; top: number } | null>(null);
  const [hidden, setHidden] = useState<Record<string, boolean>>(
    () => Object.fromEntries(data.map((d) => [d.id, !!d.hidden]))
  );

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

  // Prepare slices (palette + sorting + visibility)
  const slices = useMemo(() => {
    const withColors = data.map((d, i) => ({
      ...d,
      color: d.color || PALETTE[i % PALETTE.length],
    }));
    let arr = withColors.slice();
    if (sort === "asc") arr.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.value - b.value);
    else if (sort === "desc")
      arr.sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || b.value - a.value);
    else arr.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
    return arr;
  }, [data, sort]);

  const vis = useMemo(() => slices.filter((s) => !hidden[s.id] && s.value > 0), [slices, hidden]);

  const { cx, cy, rOuter, rInner } = useMemo(() => {
    const w = Math.max(0, width - MARGINS.left - MARGINS.right);
    const h = Math.max(0, height - MARGINS.top - MARGINS.bottom);
    const size = Math.min(w, h);
    const rO = (size / 2) * 0.96; // slight padding
    const rI = Math.max(6, rO * innerRadiusRatio);
    const cX = MARGINS.left + w / 2;
    const cY = MARGINS.top + h / 2;
    return { cx: cX, cy: cY, rOuter: rO, rInner: rI };
  }, [width, height, innerRadiusRatio]);

  // Build arcs
  const prepared = useMemo(() => {
    const total = vis.reduce((a, b) => a + b.value, 0);
    const start = deg2rad(startAngleDeg);
    const full = Math.PI * 2;
    const pad = Math.max(0, (padAngleDeg * Math.PI) / 180);
    const n = vis.length;
    const padTotal = n > 0 ? pad * n : 0;
    const spanAvailable = Math.max(0, full - padTotal);
    let angle = start;

    const arcs = vis.map((s) => {
      const frac = total ? s.value / total : 0;
      const span = spanAvailable * frac;
      const a0 = angle + pad / 2;
      const a1 = angle + pad / 2 + span;
      angle += pad + span;
      return {
        id: s.id,
        name: s.name || s.id,
        value: s.value,
        color: s.color!,
        a0,
        a1,
        // centroid for tooltip
        centroid: polar(cx, cy, (rOuter + rInner) / 2, (a0 + a1) / 2),
      };
    });

    return { total, arcs };
  }, [vis, cx, cy, rOuter, rInner, startAngleDeg, padAngleDeg]);

  const totalFmt = useMemo(
    () => centerFormatter || ((v: number) => formatNumber(v)),
    [centerFormatter]
  );

  const selected = useMemo(() => {
    const id = hoverId ?? selectedId ?? null;
    if (!id) return null;
    const s = slices.find((x) => x.id === id) || null;
    return s && !hidden[s.id] ? s : null;
  }, [hoverId, selectedId, slices, hidden]);

  function handleSliceEnter(id: string, centroid: { x: number; y: number }) {
    setHoverId(id);
    // position tooltip near centroid
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    setTooltip({ left: rect.left + centroid.x, top: rect.top + centroid.y });
  }
  function handleSliceMove(centroid: { x: number; y: number }) {
    if (!svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    setTooltip({ left: rect.left + centroid.x, top: rect.top + centroid.y });
  }
  function handleSliceLeave() {
    setHoverId(null);
    setTooltip(null);
  }
  function handleSliceClick(id: string) {
    onSelect?.(id);
  }

  return (
    <div ref={wrapRef} className={cn("ow-chart-card", className)}>
      {legend && (
        <div className="ow-legend" role="list">
          {slices.map((s, i) => (
            <button
              key={s.id}
              role="listitem"
              type="button"
              className={cn("ow-legend-item", hidden[s.id] && "is-hidden")}
              onClick={() => setHidden((h) => ({ ...h, [s.id]: !h[s.id] }))}
              title={hidden[s.id] ? "Show" : "Hide"}
            >
              <span
                className="ow-legend-dot"
                style={{
                  // @ts-expect-error CSS var accepted
                  "--dot": s.color || PALETTE[i % PALETTE.length],
                }}
              />
              <span className="ow-legend-label">{s.name || s.id}</span>
            </button>
          ))}
        </div>
      )}

      <svg
        ref={svgRef}
        width={width}
        height={height}
        role="img"
        aria-label="Donut chart"
        className="ow-chart-svg"
      >
        {/* Donut */}
        <g>
          {prepared.arcs.length === 0 && (
            <text
              x={cx}
              y={cy}
              textAnchor="middle"
              className="ow-empty"
            >
              No data
            </text>
          )}

          {prepared.arcs.map((a) => {
            const isHover = hoverId === a.id;
            const isSelected = selectedId === a.id;
            const outerBoost = isHover || isSelected ? 4 : 0;
            const path = donutSegmentPath(cx, cy, rOuter + outerBoost, rInner, a.a0, a.a1, roundedCaps);

            return (
              <path
                key={a.id}
                d={path}
                fill={a.color}
                opacity={hidden[a.id] ? 0.15 : 1}
                onMouseEnter={() => handleSliceEnter(a.id, a.centroid)}
                onMouseMove={() => handleSliceMove(a.centroid)}
                onMouseLeave={handleSliceLeave}
                onClick={() => handleSliceClick(a.id)}
              >
                <title>{`${a.name}: ${valueLabel(a.value, prepared.total, showPercent, decimals)}`}</title>
              </path>
            );
          })}
        </g>

        {/* Center summary */}
        <g>
          <circle cx={cx} cy={cy} r={Math.max(0, rInner - 1)} fill="transparent" />
          <text x={cx} y={cy - 6} textAnchor="middle" className="ow-center-title">
            {selected ? (selected.name || selected.id) : centerTitle}
          </text>
          <text x={cx} y={cy + 14} textAnchor="middle" className="ow-center-value">
            {selected
              ? valueLabel(selected.value, prepared.total, showPercent, decimals)
              : totalFmt(prepared.total)}
          </text>
        </g>
      </svg>

      {/* Tooltip */}
      {tooltip && selected && hoverId && (
        <div
          className="ow-tooltip"
          style={{ left: tooltip.left + 10, top: tooltip.top - 10 }}
          role="tooltip"
        >
          <div className="ow-tooltip-title">{selected.name || selected.id}</div>
          <div className="ow-tooltip-sub">
            {valueLabel(selected.value, prepared.total, showPercent, decimals)}
          </div>
        </div>
      )}

      <style>{styles}</style>
    </div>
  );
}

/* ------------------------------- Geometry ---------------------------------- */

function deg2rad(d: number): number {
  return (d * Math.PI) / 180;
}

function polar(cx: number, cy: number, r: number, a: number) {
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

/**
 * Returns a donut (ring) segment path between angles a0..a1 (radians).
 * Supports rounded caps by drawing small circles at arc ends and uniting with outer/inner arcs.
 * For robustness, spans are clamped to < 2π.
 */
function donutSegmentPath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  a0: number,
  a1: number,
  rounded: boolean
): string {
  const eps = 1e-6;
  let aStart = a0;
  let aEnd = a1;
  // If full-circle (or near), trim slightly to avoid SVG arc singularity
  if (Math.abs(aEnd - aStart) >= Math.PI * 2 - 1e-4) {
    aEnd = aStart + (Math.PI * 2 - 1e-4);
  }

  const p0o = polar(cx, cy, rOuter, aStart);
  const p1o = polar(cx, cy, rOuter, aEnd);
  const p1i = polar(cx, cy, rInner, aEnd);
  const p0i = polar(cx, cy, rInner, aStart);

  const largeArc = aEnd - aStart > Math.PI ? 1 : 0;

  if (!rounded) {
    return [
      "M", p0o.x, p0o.y,
      "A", rOuter, rOuter, 0, largeArc, 1, p1o.x, p1o.y,
      "L", p1i.x, p1i.y,
      "A", rInner, rInner, 0, largeArc, 0, p0i.x, p0i.y,
      "Z",
    ].join(" ");
  }

  // Rounded caps: draw as two arcs with small circles at ends.
  // We approximate by offsetting the arc ends by a tiny epsilon toward the center,
  // then draw circular caps using arcs with radius equal to (rOuter - rInner)/2.
  const capR = Math.max(0.0001, (rOuter - rInner) / 2);
  const ro = rOuter - capR;
  const ri = rInner + capR;
  const q0o = polar(cx, cy, ro, aStart);
  const q1o = polar(cx, cy, ro, aEnd);
  const q1i = polar(cx, cy, ri, aEnd);
  const q0i = polar(cx, cy, ri, aStart);

  const largeArcRounded = aEnd - aStart > Math.PI ? 1 : 0;

  return [
    // start outer arc (with reduced radius)
    "M", q0o.x, q0o.y,
    "A", ro, ro, 0, largeArcRounded, 1, q1o.x, q1o.y,
    // end cap (outer -> inner) clockwise
    "A", capR, capR, 0, 0, 1, q1i.x, q1i.y,
    // inner arc back
    "A", ri, ri, 0, largeArcRounded, 0, q0i.x, q0i.y,
    // start cap (inner -> outer)
    "A", capR, capR, 0, 0, 1, q0o.x, q0o.y,
    "Z",
  ].join(" ");
}

/* -------------------------------- Formatters -------------------------------- */

function valueLabel(value: number, total: number, showPercent: boolean, decimals: number): string {
  if (total <= 0) return formatNumber(value);
  if (!showPercent) return formatNumber(value);
  const pct = (value / total) * 100;
  return `${pct.toFixed(decimals)}%`;
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

/* --------------------------------- Styles ----------------------------------- */

const styles = `
.ow-chart-card {
  position: relative;
  border: 1px solid var(--border, rgba(255,255,255,.12));
  border-radius: 12px;
  background: var(--panel, #0b1020);
  padding: 8px;
}

.ow-chart-svg { display: block; width: 100%; height: auto; }
.ow-empty { fill: var(--muted, #9aa4b2); font-size: 12px; }

.ow-legend {
  display: flex; flex-wrap: wrap; gap: 6px 12px;
  padding: 4px 6px 8px 6px;
}
.ow-legend-item {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 4px 8px; border-radius: 8px;
  border: 1px solid var(--border, rgba(255,255,255,.12));
  background: transparent; color: var(--fg, #e5e7eb);
  cursor: pointer; font-size: 12px;
}
.ow-legend-item.is-hidden { opacity: .5; }
.ow-legend-item:hover { background: rgba(255,255,255,.05); }
.ow-legend-dot { width: 10px; height: 10px; border-radius: 999px; background: var(--dot, #6b7280); }

.ow-center-title {
  fill: var(--muted, #9aa4b2);
  font-size: 12px;
}
.ow-center-value {
  fill: var(--fg, #e5e7eb);
  font-size: 14px;
  font-weight: 600;
}

.ow-tooltip {
  position: fixed;
  transform: translate(-50%, -100%);
  background: rgba(17,24,39,.95);
  color: #e5e7eb;
  border: 1px solid rgba(255,255,255,.12);
  border-radius: 8px;
  padding: 8px 10px;
  pointer-events: none;
  box-shadow: 0 8px 20px rgba(0,0,0,.35);
  z-index: 50;
  min-width: 120px;
}
.ow-tooltip-title { font-weight: 600; font-size: 12px; margin-bottom: 2px; }
.ow-tooltip-sub { font-size: 12px; color: #cbd5e1; }
`;

