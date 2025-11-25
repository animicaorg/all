import React, { useEffect, useMemo, useRef, useState } from "react";
import cn from "../../utils/classnames";

/**
 * GaugeChart — utilization/health gauge
 * - Pure SVG, responsive via ResizeObserver (width = container width)
 * - Semi- or full-circle modes (default: 240° sweep, -120..+120 deg)
 * - Threshold segments (OK/Warn/Danger) or custom segments
 * - Ticks, animated needle, center readout, accessibility
 */

type Num = number;

export type GaugeSegment = {
  id: string;
  from: number; // in value space
  to: number;   // in value space
  color?: string; // CSS color or var
  label?: string;
};

export interface GaugeChartProps {
  value: number;
  min?: number;
  max?: number;

  /** Provide either thresholds or explicit segments. If both, segments win. */
  thresholds?: {
    warn: number;   // value >= warn => warning (inclusive lower bound)
    danger: number; // value >= danger => danger (inclusive lower bound)
  };
  segments?: GaugeSegment[];

  /** Geometry / visuals */
  height?: number;            // outer SVG height
  className?: string;
  startAngleDeg?: number;     // default -120
  endAngleDeg?: number;       // default +120
  thickness?: number;         // ring thickness (px), default 16
  padAngleDeg?: number;       // gap between segments (deg), default 1
  roundedCaps?: boolean;      // rounded segment ends, default true
  showTicks?: boolean;        // major ticks, default true
  tickCount?: number;         // number of major ticks (including ends), default 7
  tickSize?: number;          // px outward from inner radius, default 8
  animate?: boolean;          // needle animation, default true

  /** Labels / formatting */
  label?: string;             // title above readout
  sublabel?: string;          // small text under readout
  format?: (v: number) => string; // readout formatter
  decimals?: number;          // fallback decimals if no formatter

  /** Legend */
  legend?: boolean;           // show legend for segments
}

const DEFAULTS = {
  height: 220,
  startAngleDeg: -120,
  endAngleDeg: 120,
  thickness: 16,
  padAngleDeg: 1,
  roundedCaps: true,
  showTicks: true,
  tickCount: 7,
  tickSize: 8,
  animate: true,
  decimals: 1,
  thresholds: { warn: 0.75, danger: 0.9 }, // if used as ratios (we convert to absolute)
};

// Palette via CSS variables (with fallbacks)
const COLORS = {
  track: "var(--gauge-track, #1f2937)",
  ok: "var(--gauge-ok, #22c55e)",
  warn: "var(--gauge-warn, #f59e0b)",
  danger: "var(--gauge-danger, #ef4444)",
  needle: "var(--gauge-needle, #e5e7eb)",
  text: "var(--fg, #e5e7eb)",
  muted: "var(--muted, #9aa4b2)",
  border: "var(--border, rgba(255,255,255,.12))",
  panel: "var(--panel, #0b1020)",
};

export default function GaugeChart({
  value,
  min = 0,
  max = 100,
  thresholds,
  segments,
  height = DEFAULTS.height,
  className,
  startAngleDeg = DEFAULTS.startAngleDeg,
  endAngleDeg = DEFAULTS.endAngleDeg,
  thickness = DEFAULTS.thickness,
  padAngleDeg = DEFAULTS.padAngleDeg,
  roundedCaps = DEFAULTS.roundedCaps,
  showTicks = DEFAULTS.showTicks,
  tickCount = DEFAULTS.tickCount,
  tickSize = DEFAULTS.tickSize,
  animate = DEFAULTS.animate,
  label,
  sublabel,
  format,
  decimals = DEFAULTS.decimals,
  legend = true,
}: GaugeChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<Num>(480);

  useEffect(() => {
    if (!wrapRef.current) return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      if (r && r.width > 0) setWidth(Math.floor(r.width));
    });
    ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  const clamped = useMemo(() => clamp(value, min, max), [value, min, max]);
  const sweepRad = useMemo(
    () => deg2rad(endAngleDeg - startAngleDeg),
    [startAngleDeg, endAngleDeg]
  );

  const bounds = useMemo(() => {
    // Compute square that contains the arc; we keep simple margins
    const w = width;
    const h = height;
    const size = Math.min(w, h);
    const margin = 8;
    const rOuter = size / 2 - margin;
    const rInner = Math.max(4, rOuter - thickness);
    const cx = w / 2;
    const cy = h / 2;
    return { w, h, cx, cy, rOuter, rInner };
  }, [width, height, thickness]);

  const segs: GaugeSegment[] = useMemo(() => {
    if (segments && segments.length) {
      // sanitize & clamp
      return segments
        .map((s, i) => ({
          id: s.id ?? `seg-${i}`,
          from: clamp(s.from, min, max),
          to: clamp(s.to, min, max),
          color: s.color,
          label: s.label,
        }))
        .filter((s) => s.to > s.from)
        .sort((a, b) => a.from - b.from);
    }
    // Build from thresholds; if thresholds look like ratios (0..1), convert to absolute
    const t = thresholds ?? DEFAULTS.thresholds;
    const warnAbs = isRatio(t.warn) ? min + (max - min) * t.warn : t.warn;
    const dangerAbs = isRatio(t.danger) ? min + (max - min) * t.danger : t.danger;

    const okTo = clamp(warnAbs, min, max);
    const warnTo = clamp(dangerAbs, min, max);

    const list: GaugeSegment[] = [
      { id: "ok", from: min, to: okTo, color: COLORS.ok, label: "OK" },
      { id: "warn", from: okTo, to: warnTo, color: COLORS.warn, label: "Warn" },
      { id: "danger", from: warnTo, to: max, color: COLORS.danger, label: "Danger" },
    ].filter((s) => s.to > s.from);

    return list;
  }, [segments, thresholds, min, max]);

  const status = useMemo<"ok" | "warn" | "danger">(() => {
    const s = segs.find((s) => clamped >= s.from && clamped <= s.to);
    return (s?.id as any) || "ok";
  }, [segs, clamped]);

  const scale = (v: number) => {
    const t = (v - min) / Math.max(1e-9, max - min);
    return deg2rad(startAngleDeg) + sweepRad * clamp(t, 0, 1);
  };

  const arcDefs = useMemo(() => {
    const pad = (padAngleDeg * Math.PI) / 180;
    const arcs = segs.map((s) => {
      const a0 = scale(s.from) + pad / 2;
      const a1 = scale(s.to) - pad / 2;
      return { ...s, a0, a1 };
    });
    return arcs.filter((a) => a.a1 > a.a0);
  }, [segs, scale, padAngleDeg]);

  const readout = useMemo(() => {
    if (format) return format(clamped);
    const d = decimals;
    // if range ~[0..1] show percent
    if (max - min <= 1.0000001) return `${(clamped * 100).toFixed(d)}%`;
    return clamped.toFixed(d);
  }, [clamped, format, min, max, decimals]);

  const needleAngle = scale(clamped);
  const { cx, cy, rOuter, rInner } = bounds;
  const rMid = (rOuter + rInner) / 2;

  return (
    <div ref={wrapRef} className={cn("ow-gauge-card", className)}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label ? `Gauge: ${label}` : "Gauge"}
        className="ow-gauge-svg"
      >
        {/* Track */}
        <path
          d={ringPath(cx, cy, rOuter, rInner, deg2rad(startAngleDeg), deg2rad(endAngleDeg), false)}
          fill={COLORS.track}
          opacity={0.35}
        />

        {/* Segments */}
        {arcDefs.map((a) => (
          <path
            key={a.id}
            d={ringPath(cx, cy, rOuter, rInner, a.a0, a.a1, roundedCaps)}
            fill={a.color || segmentFallback(a.id)}
          >
            <title>
              {a.label || a.id}: {a.from} – {a.to}
            </title>
          </path>
        ))}

        {/* Ticks */}
        {showTicks &&
          ticks({ count: tickCount, min, max }).map((t) => {
            const A = scale(t.value);
            const p0 = polar(cx, cy, rInner - 2, A);
            const p1 = polar(cx, cy, rInner - 2 - tickSize, A);
            return (
              <line
                key={`tick-${t.value}`}
                x1={p0.x}
                y1={p0.y}
                x2={p1.x}
                y2={p1.y}
                stroke={COLORS.muted}
                strokeWidth={1}
                opacity={0.8}
              />
            );
          })}

        {/* Needle */}
        <g
          className={cn("ow-needle", animate && "is-anim")}
          style={{
            transformOrigin: `${cx}px ${cy}px`,
            transform: `rotate(${rad2deg(needleAngle)}deg)`,
          }}
        >
          {/* shaft */}
          <line
            x1={cx}
            y1={cy}
            x2={cx + (rMid - 2) * Math.cos(0)}
            y2={cy + (rMid - 2) * Math.sin(0)}
            stroke={COLORS.needle}
            strokeWidth={2}
          />
        </g>

        {/* Needle cap */}
        <circle cx={cx} cy={cy} r={Math.max(3, thickness * 0.35)} fill={COLORS.needle} opacity={0.9} />

        {/* Labels */}
        {label && (
          <text x={cx} y={cy - thickness * 1.2} textAnchor="middle" className="ow-gauge-label">
            {label}
          </text>
        )}
        <text x={cx} y={cy + 6} textAnchor="middle" className={cn("ow-gauge-readout", `st-${status}`)}>
          {readout}
        </text>
        {sublabel && (
          <text x={cx} y={cy + 24} textAnchor="middle" className="ow-gauge-sublabel">
            {sublabel}
          </text>
        )}
      </svg>

      {legend && arcDefs.length > 0 && (
        <div className="ow-gauge-legend" role="list">
          {arcDefs.map((a) => (
            <div className="ow-gauge-legend-item" role="listitem" key={`legend-${a.id}`}>
              <span
                className="ow-gauge-legend-dot"
                style={{ background: a.color || segmentFallback(a.id) }}
              />
              <span className="ow-gauge-legend-label">
                {a.label || a.id}
              </span>
              <span className="ow-gauge-legend-range">
                {fmtNumber(a.from)}–{fmtNumber(a.to)}
              </span>
            </div>
          ))}
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
function rad2deg(r: number): number {
  return (r * 180) / Math.PI;
}
function clamp(n: number, a: number, b: number) {
  return Math.min(b, Math.max(a, n));
}
function isRatio(n: number) {
  return n > 0 && n <= 1;
}
function polar(cx: number, cy: number, r: number, a: number) {
  return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
}

function ringPath(
  cx: number,
  cy: number,
  rOuter: number,
  rInner: number,
  a0: number,
  a1: number,
  rounded: boolean
): string {
  // Avoid full circle singularity
  const eps = 1e-4;
  if (Math.abs(a1 - a0) >= Math.PI * 2) a1 = a0 + (Math.PI * 2 - eps);

  if (!rounded) {
    const p0o = polar(cx, cy, rOuter, a0);
    const p1o = polar(cx, cy, rOuter, a1);
    const p1i = polar(cx, cy, rInner, a1);
    const p0i = polar(cx, cy, rInner, a0);
    const largeArc = a1 - a0 > Math.PI ? 1 : 0;
    return [
      "M", p0o.x, p0o.y,
      "A", rOuter, rOuter, 0, largeArc, 1, p1o.x, p1o.y,
      "L", p1i.x, p1i.y,
      "A", rInner, rInner, 0, largeArc, 0, p0i.x, p0i.y,
      "Z",
    ].join(" ");
  }

  // Rounded caps: inset radii by capR and add arc caps
  const capR = Math.max(0.0001, (rOuter - rInner) / 2);
  const ro = rOuter - capR;
  const ri = rInner + capR;
  const q0o = polar(cx, cy, ro, a0);
  const q1o = polar(cx, cy, ro, a1);
  const q1i = polar(cx, cy, ri, a1);
  const q0i = polar(cx, cy, ri, a0);
  const largeArc = a1 - a0 > Math.PI ? 1 : 0;

  return [
    "M", q0o.x, q0o.y,
    "A", ro, ro, 0, largeArc, 1, q1o.x, q1o.y,
    "A", capR, capR, 0, 0, 1, q1i.x, q1i.y,
    "A", ri, ri, 0, largeArc, 0, q0i.x, q0i.y,
    "A", capR, capR, 0, 0, 1, q0o.x, q0o.y,
    "Z",
  ].join(" ");
}

/* --------------------------------- Ticks ----------------------------------- */

function ticks({
  count,
  min,
  max,
}: {
  count: number;
  min: number;
  max: number;
}) {
  const n = Math.max(2, Math.floor(count));
  const arr: { value: number }[] = [];
  for (let i = 0; i < n; i++) {
    const t = i / (n - 1);
    arr.push({ value: min + (max - min) * t });
  }
  return arr;
}

/* -------------------------------- Formatters -------------------------------- */

function fmtNumber(n: number): string {
  const a = Math.abs(n);
  if (a >= 1_000_000_000) return (n / 1_000_000_000).toFixed(2).replace(/\.00$/, "") + "B";
  if (a >= 1_000_000) return (n / 1_000_000).toFixed(2).replace(/\.00$/, "") + "M";
  if (a >= 1_000) return (n / 1_000).toFixed(2).replace(/\.00$/, "") + "k";
  if (a >= 1) return n.toFixed(2).replace(/\.00$/, "");
  if (a === 0) return "0";
  return n.toPrecision(3);
}
function segmentFallback(id: string): string {
  if (id === "ok") return COLORS.ok;
  if (id === "warn") return COLORS.warn;
  if (id === "danger") return COLORS.danger;
  return COLORS.ok;
}

/* --------------------------------- Styles ----------------------------------- */

const styles = `
.ow-gauge-card {
  position: relative;
  border: 1px solid ${COLORS.border};
  border-radius: 12px;
  background: ${COLORS.panel};
  padding: 8px;
}

.ow-gauge-svg { display: block; width: 100%; height: auto; }

.ow-gauge-label {
  fill: ${COLORS.muted};
  font-size: 12px;
}
.ow-gauge-readout {
  font-weight: 700;
  font-size: 18px;
}
.ow-gauge-readout.st-ok { fill: ${COLORS.ok}; }
.ow-gauge-readout.st-warn { fill: ${COLORS.warn}; }
.ow-gauge-readout.st-danger { fill: ${COLORS.danger}; }

.ow-gauge-sublabel {
  fill: ${COLORS.muted};
  font-size: 12px;
}

.ow-needle.is-anim {
  transition: transform 260ms cubic-bezier(.2,.9,.25,1);
}

.ow-gauge-legend {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  padding: 6px 8px 2px 8px;
}
.ow-gauge-legend-item {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 2px 8px; border-radius: 8px;
  border: 1px solid ${COLORS.border};
  color: ${COLORS.text};
  font-size: 12px;
}
.ow-gauge-legend-dot {
  width: 10px; height: 10px; border-radius: 999px; background: ${COLORS.ok};
}
.ow-gauge-legend-label { opacity: .9; }
.ow-gauge-legend-range { opacity: .7; margin-left: 6px; }
`;

