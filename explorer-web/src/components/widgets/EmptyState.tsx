import React from "react";
import { classNames } from "../../utils/classnames";

type Size = "sm" | "md" | "lg";
type Align = "left" | "center";

export interface EmptyStateProps {
  /** Main headline text or custom node */
  title: React.ReactNode;
  /** Supporting text */
  description?: React.ReactNode;
  /** Optional icon node rendered above the title (e.g. <SvgIcon />) */
  icon?: React.ReactNode;
  /** Optional illustration image */
  illustrationSrc?: string;
  /** Alt text for the illustration image */
  illustrationAlt?: string;
  /** Primary/secondary actions (buttons/links) */
  actions?: React.ReactNode;
  /** Component size (spacing & typography scale) */
  size?: Size;
  /** Content alignment */
  align?: Align;
  /** Draw a subtle border */
  bordered?: boolean;
  /** Add a light shadow */
  elevated?: boolean;
  /** Make the container take full height and center contents */
  fullHeight?: boolean;
  /** Additional class name(s) */
  className?: string;
  /** Inline styles */
  style?: React.CSSProperties;
  /** Test id */
  "data-testid"?: string;
}

const SIZE = {
  sm: { pad: 16, gap: 10, icon: 28, title: 16, desc: 13 },
  md: { pad: 20, gap: 12, icon: 40, title: 18, desc: 14 },
  lg: { pad: 24, gap: 14, icon: 56, title: 20, desc: 15 },
} as const;

function DefaultIcon({ size }: { size: number }) {
  // Simple neutral placeholder icon
  const s = size;
  const r = s / 2 - 2;
  return (
    <svg
      width={s}
      height={s}
      viewBox={`0 0 ${s} ${s}`}
      aria-hidden="true"
      focusable="false"
    >
      <circle
        cx={s / 2}
        cy={s / 2}
        r={r}
        fill="none"
        stroke="var(--border-2, #d8dbe0)"
        strokeWidth="2"
      />
      <path
        d={`M ${s / 2} ${s / 2 - r / 2} v ${r}`}
        stroke="var(--text-3, #6b7280)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <path
        d={`M ${s / 2 - r / 2} ${s / 2} h ${r}`}
        stroke="var(--text-3, #6b7280)"
        strokeWidth="2"
        strokeLinecap="round"
      />
    </svg>
  );
}

/**
 * EmptyState — consistent, accessible empty/prompt view for tables, lists, pages.
 *
 * Uses design tokens when available:
 *   --surface-1, --surface-2, --border-1/2, --text-1/2/3, --radius-2
 */
export function EmptyState({
  title,
  description,
  icon,
  illustrationSrc,
  illustrationAlt,
  actions,
  size = "md",
  align = "center",
  bordered = true,
  elevated = false,
  fullHeight = false,
  className,
  style,
  "data-testid": dataTestId,
}: EmptyStateProps) {
  const S = SIZE[size] ?? SIZE.md;

  const containerStyles: React.CSSProperties = {
    padding: S.pad,
    gap: S.gap,
    textAlign: align === "center" ? "center" : "left",
    background: "var(--surface-1, #fff)",
    border: bordered ? "1px solid var(--border-1, #e5e7eb)" : "none",
    borderRadius: "var(--radius-2, 10px)",
    boxShadow: elevated ? "0 1px 2px rgba(0,0,0,.06)" : "none",
    display: "flex",
    flexDirection: "column",
    alignItems: align === "center" ? "center" : "flex-start",
    justifyContent: fullHeight ? "center" : "flex-start",
    minHeight: fullHeight ? 220 : undefined,
    ...style,
  };

  const titleStyle: React.CSSProperties = {
    fontSize: `clamp(${S.title}px, ${S.title}px, ${S.title + 1}px)`,
    lineHeight: 1.25,
    color: "var(--text-1, #111827)",
    fontWeight: 600,
    margin: 0,
  };

  const descStyle: React.CSSProperties = {
    fontSize: S.desc,
    color: "var(--text-2, #374151)",
    margin: 0,
    maxWidth: 640,
  };

  const iconWrapStyle: React.CSSProperties = {
    width: S.icon + 16,
    height: S.icon + 16,
    borderRadius: "9999px",
    background: "var(--surface-2, #f9fafb)",
    border: "1px solid var(--border-1, #e5e7eb)",
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
  };

  const illusStyle: React.CSSProperties = {
    maxWidth: 320,
    width: "100%",
    height: "auto",
    borderRadius: "var(--radius-2, 10px)",
    border: "1px dashed var(--border-1, #e5e7eb)",
    background: "var(--surface-2, #f9fafb)",
  };

  return (
    <section
      role="region"
      aria-label={
        typeof title === "string" ? `Empty: ${title}` : "Empty state section"
      }
      className={classNames("EmptyState", className)}
      style={containerStyles}
      data-testid={dataTestId}
    >
      {(icon || illustrationSrc) && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: align === "center" ? "center" : "flex-start",
            gap: S.gap,
          }}
        >
          {illustrationSrc ? (
            <img
              src={illustrationSrc}
              alt={illustrationAlt ?? ""}
              loading="lazy"
              decoding="async"
              style={illusStyle}
            />
          ) : (
            <div style={iconWrapStyle}>
              {icon ?? <DefaultIcon size={S.icon} />}
            </div>
          )}
        </div>
      )}

      <h2 style={titleStyle}>{title}</h2>

      {description && (
        <p style={descStyle} aria-live="polite">
          {description}
        </p>
      )}

      {actions && (
        <div
          className="actions"
          style={{
            display: "flex",
            gap: 8,
            marginTop: 4,
            flexWrap: "wrap",
            alignItems: "center",
            justifyContent: align === "center" ? "center" : "flex-start",
          }}
        >
          {actions}
        </div>
      )}
    </section>
  );
}

export default EmptyState;
