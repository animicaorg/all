import React from "react";
import { classNames } from "../../utils/classnames";

type SizeToken = "xs" | "sm" | "md" | "lg" | "xl";

export interface SpinnerProps {
  /** Pixel size or token (xs/sm/md/lg/xl). Default: md (24px). */
  size?: number | SizeToken;
  /** Stroke width (px). Default: auto based on size. */
  thickness?: number;
  /** Accessible label. If omitted, a default will be used. */
  label?: string;
  /** Animation speed in seconds per full rotation. Default: 0.9s. */
  speed?: number;
  /** Spinner color (CSS color). Defaults to var(--brand-600) / currentColor. */
  color?: string;
  /** Track (background ring) color. Defaults to var(--border-1). */
  trackColor?: string;
  /** Render inline (baseline-aligned) instead of block. */
  inline?: boolean;
  /** Additional class names. */
  className?: string;
  /** Inline styles for the outer wrapper. */
  style?: React.CSSProperties;
}

const SIZE_MAP: Record<SizeToken, number> = {
  xs: 14,
  sm: 18,
  md: 24,
  lg: 32,
  xl: 40,
};

export function Spinner({
  size = "md",
  thickness,
  label = "Loading…",
  speed = 0.9,
  color,
  trackColor,
  inline = false,
  className,
  style,
}: SpinnerProps) {
  const px = typeof size === "number" ? size : SIZE_MAP[size] ?? SIZE_MAP.md;
  const strokeWidth =
    thickness !== undefined
      ? thickness
      : px <= 16
      ? 2
      : px <= 24
      ? 2.5
      : px <= 32
      ? 3
      : 3.5;

  // Defaults use design tokens where available; head inherits `color` (or currentColor).
  const headColor = color ?? "currentColor";
  const bgColor = trackColor ?? "var(--border-1)";

  // Circle radius: keep padding so stroke doesn't clip.
  const r = 25 - strokeWidth / 2;

  return (
    <span
      role="status"
      aria-live="polite"
      aria-busy="true"
      aria-label={label}
      className={classNames("spinner", inline ? "inline" : "block", className)}
      style={{ width: px, height: px, color: headColor, ...style }}
    >
      <svg
        className="svg"
        viewBox="0 0 50 50"
        width={px}
        height={px}
        focusable="false"
        aria-hidden="true"
      >
        <circle
          className="track"
          cx="25"
          cy="25"
          r={r}
          fill="none"
          stroke={bgColor}
          strokeWidth={strokeWidth}
        />
        <circle
          className="head"
          cx="25"
          cy="25"
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          // Dash pattern: visible arc + gap
          strokeDasharray="80 200"
          strokeDashoffset="0"
        />
      </svg>
      <span className="sr-only">{label}</span>

      <style jsx>{`
        .spinner {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          vertical-align: middle;
        }
        .spinner.block {
          display: inline-flex;
        }
        .spinner.inline {
          display: inline-flex;
        }
        .svg {
          animation: spinner-rotate ${speed}s linear infinite;
          transform-origin: center;
        }
        .head {
          animation: spinner-dash ${Math.max(1.4, speed * 1.5)}s ease-in-out
            infinite;
        }
        @keyframes spinner-rotate {
          100% {
            transform: rotate(360deg);
          }
        }
        @keyframes spinner-dash {
          0% {
            stroke-dasharray: 1, 200;
            stroke-dashoffset: 0;
          }
          50% {
            stroke-dasharray: 90, 200;
            stroke-dashoffset: -35px;
          }
          100% {
            stroke-dasharray: 90, 200;
            stroke-dashoffset: -124px;
          }
        }
        .sr-only {
          position: absolute;
          width: 1px;
          height: 1px;
          padding: 0;
          margin: -1px;
          overflow: hidden;
          clip: rect(0, 0, 0, 0);
          white-space: nowrap;
          border: 0;
        }
      `}</style>
    </span>
  );
}

export default Spinner;
