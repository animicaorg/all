import React, { useMemo, useId } from "react";
import cn from "../../utils/classnames";

/**
 * Sparkline — tiny trend chart (for tables & list cells)
 * - Pure SVG, no deps, works in tight spaces
 * - Auto color by delta (+/-/0) unless overridden
 * - Optional area fill and end-point dot
 */

type Pt = number | { x: number; y: number };

export interface SparklineProps {
  data: Pt[];                  // numbers are treated as y with implicit x=index
  width?: number;              // default 100
  height?: number;             // default 28
  min?: number;                // clamp y-min (else auto)
  max?: number;                // clamp y-max (else auto)
  smooth?: boolean;            // Catmull-Rom smoothing (default true)
  showArea?: boolean;          // fill under the curve (default true)
  showDot?: boolean;           // end-point dot (default true)
  className?: string;

  /** Colors (CSS vars with sensible fallbacks) */
  stroke?: string;             // overrides auto-color if provided
  fill?: string;               // overrides gradient if provided
  positiveColor?: string;      // default var(--spark-pos, #22c55e)
  negativeColor?: string;      // default var(--spark-neg, #ef4444)
  neutralColor?: string;       // default var(--spark-neutral, #9aa4b2)

  strokeWidth?: number;        // default 1.5
  ariaLabel?: string;          // a11y label
}

const DEF = {
  width: 100,
  height: 28,
  smooth: true,
  showArea: true,
  showDot: true,
  strokeWidth: 1.5,
  positiveColor: "var(--spark-pos, #22c55e)",
  negativeColor: "var(--spark-neg, #ef4444)",
  neutralColor:  "var(--spark-neutral, #9aa4b2)",
  track: "var(--spark-track, rgba(255,255,255,.08))",
};

export default function Sparkline({
  data,
  width = DEF.width,
  height = DEF.height,
  min,
  max,
  smooth = DEF.smooth,
  showArea = DEF.showArea,
  showDot = DEF.showDot,
  className,
  stroke,
  fill,
  positiveColor = DEF.positiveColor,
  negativeColor = DEF.negativeColor,
  neutralColor = DEF.neutralColor,
  strokeWidth = DEF.strokeWidth,
  ariaLabel = "Sparkline",
}: SparklineProps) {
  const gradId = useId().replace(/:/g, "_");

  const parsed = useMemo(() => normalize(data), [data]);

  const yMinAuto = parsed.yMin;
  const yMaxAuto = parsed.yMax;

  const yMin = min ?? yMinAuto;
  const yMax = max ?? yMaxAuto;

  // Avoid zero-range (flat lines)
  const yRange = yMax - yMin || Math.max(1e-9, Math.abs(yMax) || 1);
  const pad = 2; // inner padding to keep stroke fully visible

  const scaleX = (x: number) =>
    pad + ((x - parsed.xMin) / Math.max(1e-9, parsed.xMax - parsed.xMin)) * (width - pad * 2);
  const scaleY = (y: number) =>
    height - pad - ((y - yMin) / yRange) * (height - pad * 2);

  const pts = useMemo(
    () => parsed.points.map((p) => ({ x: scaleX(p.x), y: scaleY(p.y) })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [parsed, width, height, yMin, yMax]
  );

  const path = useMemo(() => {
    if (pts.length === 0) return "";
    if (pts.length === 1) {
      const p = pts[0];
      return `M ${p.x} ${p.y} L ${p.x + 0.01} ${p.y}`; // tiny line so stroke renders
    }
    return smooth ? pathCatmullRom(pts) : pathLinear(pts);
  }, [pts, smooth]);

  const areaPath = useMemo(() => {
    if (!showArea || pts.length < 2) return "";
    const baseY = height - pad; // baseline at bottom
    const start = `M ${pts[0].x} ${baseY}`;
    const curve = (smooth ? pathCatmullRom(pts) : pathLinear(pts)).replace(/^M[^L]*/, `L ${pts[0].x} ${pts[0].y}`);
    const end = `L ${pts[pts.length - 1].x} ${baseY} Z`;
    return [start, curve, end].join(" ");
  }, [pts, showArea, height, smooth]);

  // Auto color by delta if not explicitly provided
  const delta = parsed.points.length >= 2
    ? parsed.points[parsed.points.length - 1].y - parsed.points[0].y
    : 0;
  const autoColor = delta > 0 ? positiveColor : delta < 0 ? negativeColor : neutralColor;
  const strokeColor = stroke || autoColor;

  // End dot
  const end = pts[pts.length - 1] || null;

  return (
    <svg
      width={width}
      height={height}
      role="img"
      aria-label={ariaLabel}
      className={cn("sparkline", className)}
      viewBox={`0 0 ${width} ${height}`}
    >
      {/* Optional faint track (baseline) */}
      <line
        x1={pad}
        y1={height - pad}
        x2={width - pad}
        y2={height - pad}
        stroke={DEF.track}
        strokeWidth={1}
      />

      {/* Gradient for fill (if not solid fill provided) */}
      {!fill && showArea && (
        <defs>
          <linearGradient id={`spark-grad-${gradId}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={strokeColor} stopOpacity="0.35" />
            <stop offset="100%" stopColor={strokeColor} stopOpacity="0.02" />
          </linearGradient>
        </defs>
      )}

      {/* Area */}
      {showArea && areaPath && (
        <path
          d={areaPath}
          fill={fill || `url(#spark-grad-${gradId})`}
          stroke="none"
        />
      )}

      {/* Line */}
      {path && (
        <path
          d={path}
          fill="none"
          stroke={strokeColor}
          strokeWidth={strokeWidth}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}

      {/* End point */}
      {showDot && end && (
        <circle
          cx={end.x}
          cy={end.y}
          r={Math.max(1.5, strokeWidth + 0.25)}
          fill={strokeColor}
        />
      )}
    </svg>
  );
}

/* ------------------------------ Helpers ----------------------------------- */

function normalize(data: Pt[]): {
  points: { x: number; y: number }[];
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
} {
  if (!data || data.length === 0) {
    return { points: [], xMin: 0, xMax: 1, yMin: 0, yMax: 1 };
  }
  const pts: { x: number; y: number }[] =
    typeof data[0] === "number"
      ? (data as number[]).map((y, i) => ({ x: i, y }))
      : (data as { x: number; y: number }[]).slice().sort((a, b) => a.x - b.x);

  let xMin = pts[0].x;
  let xMax = pts[0].x;
  let yMin = pts[0].y;
  let yMax = pts[0].y;
  for (let i = 1; i < pts.length; i++) {
    const p = pts[i];
    if (p.x < xMin) xMin = p.x;
    if (p.x > xMax) xMax = p.x;
    if (p.y < yMin) yMin = p.y;
    if (p.y > yMax) yMax = p.y;
  }
  // Expand flat ranges slightly to avoid divide-by-zero and render a straight line
  if (xMax === xMin) xMax = xMin + 1;
  if (yMax === yMin) {
    const eps = Math.max(1e-6, Math.abs(yMax) * 1e-3 || 1e-3);
    yMin -= eps;
    yMax += eps;
  }
  return { points: pts, xMin, xMax, yMin, yMax };
}

function pathLinear(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return "";
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 1; i < pts.length; i++) {
    d += ` L ${pts[i].x} ${pts[i].y}`;
  }
  return d;
}

/**
 * Catmull-Rom to Cubic Bezier path (monotone-ish, tension=0.5-ish)
 * Produces a smooth curve that interpolates all points.
 */
function pathCatmullRom(pts: { x: number; y: number }[], tension = 0.5): string {
  if (pts.length <= 2) return pathLinear(pts);
  let d = `M ${pts[0].x} ${pts[0].y}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] || pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] || p2;

    const t = tension;
    const c1x = p1.x + ((p2.x - p0.x) / 6) * t;
    const c1y = p1.y + ((p2.y - p0.y) / 6) * t;
    const c2x = p2.x - ((p3.x - p1.x) / 6) * t;
    const c2y = p2.y - ((p3.y - p1.y) / 6) * t;

    d += ` C ${c1x} ${c1y}, ${c2x} ${c2y}, ${p2.x} ${p2.y}`;
  }
  return d;
}
