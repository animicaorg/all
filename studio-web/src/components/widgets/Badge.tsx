import React, { CSSProperties, ReactNode } from "react";

/**
 * Badge — compact status/tag component with accessible contrast and variants.
 *
 * Variants:
 *  - solid: high-contrast solid background
 *  - soft: subtle background, colored text (default)
 *  - outline: transparent with colored border
 *  - ghost: minimal; text tinted only
 *
 * Colors:
 *  - neutral | info | success | warning | danger | accent
 *
 * Sizes:
 *  - sm | md
 *
 * Usage:
 *  <Badge>Draft</Badge>
 *  <Badge color="success" variant="solid" pill>Active</Badge>
 *  <Badge as="button" onClick={...} leadingIcon={<CheckIcon/>}>Applied</Badge>
 */

export type BadgeColor =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "accent";

export type BadgeVariant = "soft" | "solid" | "outline" | "ghost";
export type BadgeSize = "sm" | "md";

export type BadgeProps<T extends "span" | "button" | "a" = "span"> = {
  as?: T;
  children?: ReactNode;
  color?: BadgeColor;
  variant?: BadgeVariant;
  size?: BadgeSize;
  pill?: boolean;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  className?: string;
  style?: CSSProperties;
  title?: string;
  disabled?: boolean;

  // Anchor props (when as="a")
  href?: T extends "a" ? string : never;
  target?: T extends "a" ? string : never;
  rel?: T extends "a" ? string : never;

  // Button props (when as="button")
  type?: T extends "button" ? "button" | "submit" | "reset" : never;
  onClick?: React.MouseEventHandler<HTMLElement>;
} & Omit<React.HTMLAttributes<HTMLElement>, "onClick">;

const palette = {
  neutral: {
    bg: "var(--badge-neutral-bg, #F3F4F6)",
    bgSoft: "var(--badge-neutral-soft, #F9FAFB)",
    text: "var(--badge-neutral-text, #111827)",
    border: "var(--badge-neutral-border, #E5E7EB)",
    solidText: "var(--badge-neutral-solid-text, #111827)",
    ghostText: "var(--badge-neutral-ghost-text, #4B5563)",
  },
  info: {
    bg: "var(--badge-info-bg, #DBEAFE)",
    bgSoft: "var(--badge-info-soft, #EFF6FF)",
    text: "var(--badge-info-text, #1D4ED8)",
    border: "var(--badge-info-border, #BFDBFE)",
    solidText: "var(--badge-info-solid-text, #0B1023)",
    ghostText: "var(--badge-info-ghost-text, #1D4ED8)",
  },
  success: {
    bg: "var(--badge-success-bg, #DCFCE7)",
    bgSoft: "var(--badge-success-soft, #ECFDF5)",
    text: "var(--badge-success-text, #047857)",
    border: "var(--badge-success-border, #BBF7D0)",
    solidText: "var(--badge-success-solid-text, #032A23)",
    ghostText: "var(--badge-success-ghost-text, #047857)",
  },
  warning: {
    bg: "var(--badge-warning-bg, #FEF3C7)",
    bgSoft: "var(--badge-warning-soft, #FFFBEB)",
    text: "var(--badge-warning-text, #92400E)",
    border: "var(--badge-warning-border, #FDE68A)",
    solidText: "var(--badge-warning-solid-text, #2E1A09)",
    ghostText: "var(--badge-warning-ghost-text, #92400E)",
  },
  danger: {
    bg: "var(--badge-danger-bg, #FEE2E2)",
    bgSoft: "var(--badge-danger-soft, #FEF2F2)",
    text: "var(--badge-danger-text, #B91C1C)",
    border: "var(--badge-danger-border, #FCA5A5)",
    solidText: "var(--badge-danger-solid-text, #2A0A0A)",
    ghostText: "var(--badge-danger-ghost-text, #B91C1C)",
  },
  accent: {
    bg: "var(--badge-accent-bg, #EDE9FE)",
    bgSoft: "var(--badge-accent-soft, #F5F3FF)",
    text: "var(--badge-accent-text, #6D28D9)",
    border: "var(--badge-accent-border, #DDD6FE)",
    solidText: "var(--badge-accent-solid-text, #140B2E)",
    ghostText: "var(--badge-accent-ghost-text, #6D28D9)",
  },
} as const;

function baseStyle(size: BadgeSize, pill: boolean, clickable: boolean): CSSProperties {
  const padY = size === "sm" ? 2 : 4; // px
  const padX = size === "sm" ? 8 : 10;
  const fontSize = size === "sm" ? 11 : 12;
  const radius = pill ? 999 : 8;

  return {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: `${padY}px ${padX}px`,
    borderRadius: radius,
    borderWidth: 1,
    borderStyle: "solid",
    fontSize,
    fontWeight: 600,
    lineHeight: 1,
    whiteSpace: "nowrap",
    userSelect: "none",
    cursor: clickable ? "pointer" : "default",
    transition: "filter 120ms ease, opacity 120ms ease, box-shadow 120ms ease",
  };
}

function variantStyle(
  variant: BadgeVariant,
  color: typeof palette.neutral,
  disabled?: boolean
): CSSProperties {
  switch (variant) {
    case "solid":
      return {
        background: color.bg,
        color: color.solidText,
        borderColor: color.bg,
        opacity: disabled ? 0.6 : 1,
        boxShadow: "inset 0 0 0 1px rgba(0,0,0,0.02)",
      };
    case "outline":
      return {
        background: "transparent",
        color: color.text,
        borderColor: color.border,
        opacity: disabled ? 0.6 : 1,
      };
    case "ghost":
      return {
        background: "transparent",
        color: color.ghostText,
        borderColor: "transparent",
        opacity: disabled ? 0.6 : 1,
      };
    case "soft":
    default:
      return {
        background: color.bgSoft,
        color: color.text,
        borderColor: color.border,
        opacity: disabled ? 0.6 : 1,
      };
  }
}

function hoverStyle(variant: BadgeVariant): CSSProperties {
  switch (variant) {
    case "solid":
      return { filter: "brightness(0.97)" };
    case "outline":
      return { filter: "brightness(0.98)" };
    case "ghost":
      return { filter: "brightness(0.96)" };
    case "soft":
    default:
      return { filter: "brightness(0.98)" };
  }
}

const iconWrapStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 14,
  height: 14,
};

export function Badge<T extends "span" | "button" | "a">(props: BadgeProps<T>) {
  const {
    as,
    children,
    color = "neutral",
    variant = "soft",
    size = "md",
    pill = false,
    leadingIcon,
    trailingIcon,
    className,
    style,
    title,
    disabled,
    onClick,
    href,
    target,
    rel,
    type,
    ...rest
  } = props as BadgeProps<any>;

  const Tag = (as ?? (onClick ? "button" : href ? "a" : "span")) as any;
  const clickable = Boolean(onClick || href);
  const pal = palette[color] ?? palette.neutral;

  const base = baseStyle(size, pill, clickable);
  const byVariant = variantStyle(variant, pal, disabled);

  const merged: CSSProperties = {
    ...base,
    ...byVariant,
    ...(style || {}),
  };

  const handleMouseEnter = (e: React.MouseEvent<HTMLElement>) => {
    if (clickable && !disabled) {
      Object.assign((e.currentTarget as HTMLElement).style, hoverStyle(variant));
    }
  };
  const handleMouseLeave = (e: React.MouseEvent<HTMLElement>) => {
    if (clickable && !disabled) {
      (e.currentTarget as HTMLElement).style.filter = "";
    }
  };

  const commonProps: any = {
    className,
    style: merged,
    title,
    "aria-disabled": disabled || undefined,
    onClick: disabled ? undefined : onClick,
    onMouseEnter: handleMouseEnter,
    onMouseLeave: handleMouseLeave,
    ...rest,
  };

  if (Tag === "a") {
    commonProps.href = href;
    if (target) commonProps.target = target;
    if (rel) commonProps.rel = rel;
  }
  if (Tag === "button") {
    commonProps.type = type ?? "button";
    commonProps.disabled = disabled;
  }

  return (
    <Tag {...commonProps}>
      {leadingIcon ? <span aria-hidden="true" style={iconWrapStyle}>{leadingIcon}</span> : null}
      <span>{children}</span>
      {trailingIcon ? <span aria-hidden="true" style={iconWrapStyle}>{trailingIcon}</span> : null}
    </Tag>
  );
}

export namespace Badge {
  export const Group: React.FC<
    { gap?: number; wrap?: boolean; className?: string; style?: CSSProperties; children?: ReactNode }
  > = ({ gap = 8, wrap = true, className, style, children }) => (
    <div
      className={className}
      style={{
        display: "flex",
        flexWrap: wrap ? "wrap" : "nowrap",
        gap,
        alignItems: "center",
        ...(style || {}),
      }}
    >
      {children}
    </div>
  );
}

export default Badge;
