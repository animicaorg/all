import React, { CSSProperties } from "react";

/**
 * Spinner — compact, accessible, zero-dependency loading indicator.
 *
 * - Uses SVG animateTransform (works in modern browsers without global CSS).
 * - Color inherits from `currentColor` by default; you can override via `color` prop.
 * - Track color is subtle and themeable; override via `trackColor` if needed.
 * - Renders inline by default; can display an optional label (right or bottom).
 */

export type SpinnerSize = "sm" | "md" | "lg" | number;
export type SpinnerLabelPlacement = "right" | "bottom";

export interface SpinnerProps {
  size?: SpinnerSize;                 // px or token (sm|md|lg)
  stroke?: number;                    // stroke width in px
  color?: string;                     // spinner stroke color (defaults to currentColor)
  trackColor?: string;                // background ring stroke color (defaults to rgba based on theme)
  inline?: boolean;                   // inline vs block flow
  label?: string;                     // optional visible label
  labelPlacement?: SpinnerLabelPlacement;
  className?: string;
  style?: CSSProperties;
  "aria-label"?: string;              // accessible label; falls back to label or "Loading…"
}

const SIZE_MAP: Record<Exclude<SpinnerSize, number>, number> = {
  sm: 16,
  md: 20,
  lg: 28,
};

export const Spinner: React.FC<SpinnerProps> = ({
  size = "md",
  stroke = 2,
  color,
  trackColor = "var(--spinner-track, rgba(0,0,0,0.12))",
  inline = true,
  label,
  labelPlacement = "right",
  className,
  style,
  "aria-label": ariaLabelProp,
}) => {
  const px = typeof size === "number" ? size : SIZE_MAP[size] ?? SIZE_MAP.md;
  const r = Math.max(0.5, (px - stroke) / 2); // inner radius
  const c = 2 * Math.PI * r;
  const dash = 0.7 * c; // arc length
  const gap = c - dash;

  const wrapperStyle: CSSProperties = {
    display: inline ? "inline-flex" : "flex",
    alignItems: "center",
    gap: 8,
    ...(labelPlacement === "bottom"
      ? { flexDirection: "column", alignItems: "flex-start", gap: 6 }
      : {}),
    ...(style || {}),
  };

  const svgStyle: CSSProperties = {
    width: px,
    height: px,
    color: color ?? "currentColor",
    display: "inline-block",
  };

  const ariaLabel = ariaLabelProp || label || "Loading…";

  return (
    <span
      role="status"
      aria-label={ariaLabel}
      aria-live="polite"
      className={className}
      style={wrapperStyle}
    >
      <svg viewBox={`0 0 ${px} ${px}`} style={svgStyle} focusable="false" aria-hidden="true">
        {/* Track */}
        <circle
          cx={px / 2}
          cy={px / 2}
          r={r}
          fill="none"
          stroke={trackColor}
          strokeWidth={stroke}
        />
        {/* Arc */}
        <circle
          cx={px / 2}
          cy={px / 2}
          r={r}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${dash} ${gap}`}
        >
          <animateTransform
            attributeName="transform"
            type="rotate"
            from={`0 ${px / 2} ${px / 2}`}
            to={`360 ${px / 2} ${px / 2}`}
            dur="0.9s"
            repeatCount="indefinite"
          />
        </circle>
      </svg>
      {label ? (
        <span
          style={{
            fontSize: 12,
            lineHeight: 1.2,
            color: "var(--text-secondary, currentColor)",
            opacity: 0.9,
            userSelect: "none",
          }}
        >
          {label}
        </span>
      ) : null}
    </span>
  );
};

export default Spinner;
