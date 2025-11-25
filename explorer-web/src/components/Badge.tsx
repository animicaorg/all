import React from "react";

type Variant = "solid" | "soft" | "outline" | "minimal";
type Color =
  | "gray"
  | "blue"
  | "green"
  | "red"
  | "yellow"
  | "purple"
  | "indigo"
  | "orange";
type Size = "sm" | "md" | "lg";

export interface BadgeProps {
  children?: React.ReactNode;
  /** Visual style variant. */
  variant?: Variant;
  /** Color family. */
  color?: Color;
  /** Size presets. */
  size?: Size;
  /** Optional dot (status indicator) at the start. */
  dot?: boolean;
  /** Optional leading icon. */
  leadingIcon?: React.ReactNode;
  /** Optional trailing icon. */
  trailingIcon?: React.ReactNode;
  /** Truncate long text with ellipsis (keeps one line). */
  truncate?: boolean;
  /** Uppercase text (useful for tags). */
  uppercase?: boolean;
  /** Title/tooltip text. */
  title?: string;
  /** Extra class name for outer element. */
  className?: string;
  /** Mark as interactive (hover/active styles). If onClick provided, becomes a button. */
  interactive?: boolean;
  /** Click handler (renders <button>). */
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  /** data-testid for testing. */
  "data-testid"?: string;
}

/** Color palette per variant, returned as CSS custom props for the component. */
function palette(variant: Variant, color: Color): React.CSSProperties {
  // Tailwind-esque palette (static hexes to avoid external deps).
  const c = {
    gray: {
      base: "#6b7280",
      strong: "#374151",
      lightBg: "#f3f4f6",
      lightBd: "#e5e7eb",
      midBd: "#d1d5db",
      text: "#111827",
    },
    blue: {
      base: "#2563eb",
      strong: "#1e40af",
      lightBg: "#eff6ff",
      lightBd: "#bfdbfe",
      midBd: "#93c5fd",
      text: "#0b1026",
    },
    green: {
      base: "#10b981",
      strong: "#065f46",
      lightBg: "#ecfdf5",
      lightBd: "#a7f3d0",
      midBd: "#6ee7b7",
      text: "#071a13",
    },
    red: {
      base: "#ef4444",
      strong: "#991b1b",
      lightBg: "#fef2f2",
      lightBd: "#fecaca",
      midBd: "#fca5a5",
      text: "#220b0b",
    },
    yellow: {
      base: "#f59e0b",
      strong: "#92400e",
      lightBg: "#fffbeb",
      lightBd: "#fde68a",
      midBd: "#fcd34d",
      text: "#221a0b",
    },
    purple: {
      base: "#7c3aed",
      strong: "#4c1d95",
      lightBg: "#f5f3ff",
      lightBd: "#ddd6fe",
      midBd: "#c4b5fd",
      text: "#1a1126",
    },
    indigo: {
      base: "#4f46e5",
      strong: "#3730a3",
      lightBg: "#eef2ff",
      lightBd: "#c7d2fe",
      midBd: "#a5b4fc",
      text: "#101225",
    },
    orange: {
      base: "#f97316",
      strong: "#7c2d12",
      lightBg: "#fff7ed",
      lightBd: "#fed7aa",
      midBd: "#fdba74",
      text: "#22150b",
    },
  } as const;

  const p = c[color];
  switch (variant) {
    case "solid":
      return {
        // bg and border use the saturated base; foreground is white.
        ["--b-bg" as any]: p.base,
        ["--b-fg" as any]: "#ffffff",
        ["--b-bd" as any]: p.base,
        ["--b-fg-muted" as any]: "#ffffff",
      };
    case "soft":
      return {
        ["--b-bg" as any]: p.lightBg,
        ["--b-fg" as any]: p.strong,
        ["--b-bd" as any]: p.lightBd,
        ["--b-fg-muted" as any]: p.strong,
      };
    case "outline":
      return {
        ["--b-bg" as any]: "transparent",
        ["--b-fg" as any]: p.strong,
        ["--b-bd" as any]: p.midBd ?? p.lightBd,
        ["--b-fg-muted" as any]: p.strong,
      };
    case "minimal":
    default:
      return {
        ["--b-bg" as any]: "transparent",
        ["--b-fg" as any]: p.strong,
        ["--b-bd" as any]: "transparent",
        ["--b-fg-muted" as any]: p.strong,
      };
  }
}

function sizeVars(size: Size): React.CSSProperties & { ["--b-radius"]?: string } {
  switch (size) {
    case "sm":
      return {
        ["--b-pad-y" as any]: "2px",
        ["--b-pad-x" as any]: "6px",
        ["--b-gap" as any]: "6px",
        ["--b-fz" as any]: "12px",
        ["--b-ico" as any]: "12px",
        ["--b-dot" as any]: "6px",
        ["--b-radius" as any]: "999px",
      };
    case "lg":
      return {
        ["--b-pad-y" as any]: "6px",
        ["--b-pad-x" as any]: "10px",
        ["--b-gap" as any]: "8px",
        ["--b-fz" as any]: "14px",
        ["--b-ico" as any]: "16px",
        ["--b-dot" as any]: "8px",
        ["--b-radius" as any]: "999px",
      };
    case "md":
    default:
      return {
        ["--b-pad-y" as any]: "4px",
        ["--b-pad-x" as any]: "8px",
        ["--b-gap" as any]: "6px",
        ["--b-fz" as any]: "12px",
        ["--b-ico" as any]: "14px",
        ["--b-dot" as any]: "7px",
        ["--b-radius" as any]: "999px",
      };
  }
}

export function Badge({
  children,
  variant = "soft",
  color = "gray",
  size = "md",
  dot,
  leadingIcon,
  trailingIcon,
  truncate,
  uppercase,
  title,
  className,
  interactive,
  onClick,
  "data-testid": testid,
}: BadgeProps) {
  const style = React.useMemo(
    () => ({ ...palette(variant, color), ...sizeVars(size) }),
    [variant, color, size]
  );

  const classes = [
    "badge",
    `v-${variant}`,
    `c-${color}`,
    `s-${size}`,
    (interactive || onClick) ? "is-interactive" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");

  const content = (
    <>
      {dot && <span className="b-dot" aria-hidden="true" />}
      {leadingIcon && <span className="b-ico" aria-hidden="true">{leadingIcon}</span>}
      <span className={`b-text ${truncate ? "truncate" : ""}`}>
        {uppercase ? String(children ?? "").toUpperCase() : children}
      </span>
      {trailingIcon && <span className="b-ico" aria-hidden="true">{trailingIcon}</span>}
    </>
  );

  const commonProps = {
    className: classes,
    style,
    title,
    "data-testid": testid,
  } as const;

  return onClick ? (
    <button type="button" {...commonProps} onClick={onClick}>
      {content}
      <style>{css}</style>
    </button>
  ) : (
    <span {...commonProps} role={interactive ? "button" : undefined} tabIndex={interactive ? 0 : undefined}>
      {content}
      <style>{css}</style>
    </span>
  );
}

const css = `
.badge {
  --b-bg: transparent;
  --b-fg: #374151;
  --b-fg-muted: #6b7280;
  --b-bd: transparent;
  --b-pad-y: 4px;
  --b-pad-x: 8px;
  --b-gap: 6px;
  --b-fz: 12px;
  --b-ico: 14px;
  --b-dot: 7px;
  --b-radius: 999px;

  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--b-gap);
  padding: var(--b-pad-y) var(--b-pad-x);
  border-radius: var(--b-radius);
  background: var(--b-bg);
  color: var(--b-fg);
  border: 1px solid var(--b-bd);
  font-size: var(--b-fz);
  line-height: 1;
  white-space: nowrap;
  user-select: none;
  font-weight: 600;
}

.badge .b-text {
  display: inline-block;
  max-width: 22ch;
}

.badge .b-text.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
}

.badge .b-ico {
  display: inline-flex;
  width: var(--b-ico);
  height: var(--b-ico);
  line-height: 0;
}

.badge .b-ico > svg {
  width: 100%;
  height: 100%;
}

.badge .b-dot {
  width: var(--b-dot);
  height: var(--b-dot);
  border-radius: 999px;
  background: currentColor;
  opacity: 0.9;
}

/* Interaction states */
.badge.is-interactive,
button.badge {
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease, transform .02s ease, color .15s ease;
}

.badge.is-interactive:hover,
button.badge:hover {
  filter: brightness(0.98);
}

.badge.is-interactive:active,
button.badge:active {
  transform: translateY(1px);
}

/* Variant-specific fine tuning */
.badge.v-outline { background: transparent; }
.badge.v-minimal { border-color: transparent; }

/* High-contrast mode hints */
@media (prefers-contrast: more) {
  .badge { border-color: var(--b-fg-muted); }
}
`;

export default Badge;

/** Convenience badges for common statuses. */
export const Status = {
  success(props: Omit<BadgeProps, "variant" | "color">) {
    return <Badge variant="soft" color="green" dot {...props} />;
  },
  warning(props: Omit<BadgeProps, "variant" | "color">) {
    return <Badge variant="soft" color="yellow" dot {...props} />;
  },
  danger(props: Omit<BadgeProps, "variant" | "color">) {
    return <Badge variant="soft" color="red" dot {...props} />;
  },
  info(props: Omit<BadgeProps, "variant" | "color">) {
    return <Badge variant="soft" color="blue" dot {...props} />;
  },
  neutral(props: Omit<BadgeProps, "variant" | "color">) {
    return <Badge variant="soft" color="gray" dot {...props} />;
  },
};
