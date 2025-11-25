import React, { CSSProperties, ReactNode, useCallback } from "react";

/**
 * Tag — selectable, optionally removable label chip.
 *
 * Features:
 *  - Variants: soft (default) | solid | outline
 *  - Colors: neutral | info | success | warning | danger | accent
 *  - Sizes: sm | md
 *  - Selected state (aria-pressed for toggles)
 *  - Removable with an accessible close button (x)
 *  - Optional leading/trailing icons
 *
 * Usage:
 *  <Tag>Beta</Tag>
 *  <Tag color="success" selected onToggle={(v)=>...}>Active</Tag>
 *  <Tag color="danger" removable onRemove={()=>...}>Filtered</Tag>
 *  <Tag as="a" href="/tags/ai" leadingIcon={<RobotIcon/>}>AI</Tag>
 */

export type TagColor =
  | "neutral"
  | "info"
  | "success"
  | "warning"
  | "danger"
  | "accent";

export type TagVariant = "soft" | "solid" | "outline";
export type TagSize = "sm" | "md";

export type TagProps<T extends "span" | "button" | "a" = "span"> = {
  as?: T;
  children?: ReactNode;
  color?: TagColor;
  variant?: TagVariant;
  size?: TagSize;
  selected?: boolean;
  pill?: boolean;

  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;

  /** Show a small close button and wire events */
  removable?: boolean;
  onRemove?: (e: React.MouseEvent<HTMLButtonElement> | React.KeyboardEvent<HTMLButtonElement>) => void;

  /** Toggle handler; if provided and "as" not set, component defaults to <button> */
  onToggle?: (selected: boolean) => void;

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
} & Omit<React.HTMLAttributes<HTMLElement>, "onClick">;

const palette = {
  neutral: {
    bg: "var(--tag-neutral-bg, #F3F4F6)",
    soft: "var(--tag-neutral-soft, #F9FAFB)",
    text: "var(--tag-neutral-text, #111827)",
    border: "var(--tag-neutral-border, #E5E7EB)",
    solidText: "var(--tag-neutral-solid-text, #111827)",
  },
  info: {
    bg: "var(--tag-info-bg, #DBEAFE)",
    soft: "var(--tag-info-soft, #EFF6FF)",
    text: "var(--tag-info-text, #1D4ED8)",
    border: "var(--tag-info-border, #BFDBFE)",
    solidText: "var(--tag-info-solid-text, #0B1023)",
  },
  success: {
    bg: "var(--tag-success-bg, #DCFCE7)",
    soft: "var(--tag-success-soft, #ECFDF5)",
    text: "var(--tag-success-text, #047857)",
    border: "var(--tag-success-border, #BBF7D0)",
    solidText: "var(--tag-success-solid-text, #032A23)",
  },
  warning: {
    bg: "var(--tag-warning-bg, #FEF3C7)",
    soft: "var(--tag-warning-soft, #FFFBEB)",
    text: "var(--tag-warning-text, #92400E)",
    border: "var(--tag-warning-border, #FDE68A)",
    solidText: "var(--tag-warning-solid-text, #2E1A09)",
  },
  danger: {
    bg: "var(--tag-danger-bg, #FEE2E2)",
    soft: "var(--tag-danger-soft, #FEF2F2)",
    text: "var(--tag-danger-text, #B91C1C)",
    border: "var(--tag-danger-border, #FCA5A5)",
    solidText: "var(--tag-danger-solid-text, #2A0A0A)",
  },
  accent: {
    bg: "var(--tag-accent-bg, #EDE9FE)",
    soft: "var(--tag-accent-soft, #F5F3FF)",
    text: "var(--tag-accent-text, #6D28D9)",
    border: "var(--tag-accent-border, #DDD6FE)",
    solidText: "var(--tag-accent-solid-text, #140B2E)",
  },
} as const;

function baseStyle(size: TagSize, pill: boolean, interactive: boolean): CSSProperties {
  const padY = size === "sm" ? 2 : 5; // px
  const padX = size === "sm" ? 8 : 10;
  const gap = size === "sm" ? 6 : 8;
  const fontSize = size === "sm" ? 11 : 12;
  const radius = pill ? 999 : 10;

  return {
    display: "inline-flex",
    alignItems: "center",
    gap,
    padding: `${padY}px ${padX}px`,
    borderRadius: radius,
    borderWidth: 1,
    borderStyle: "solid",
    fontSize,
    fontWeight: 600,
    lineHeight: 1,
    whiteSpace: "nowrap",
    userSelect: "none",
    cursor: interactive ? "pointer" : "default",
    transition: "filter 140ms ease, opacity 140ms ease, box-shadow 140ms ease",
  };
}

function variantStyle(
  variant: TagVariant,
  color: typeof palette.neutral,
  selected?: boolean,
  disabled?: boolean
): CSSProperties {
  const selBoost = selected ? 0.96 : 1;
  switch (variant) {
    case "solid":
      return {
        background: color.bg,
        color: color.solidText,
        borderColor: color.bg,
        opacity: disabled ? 0.6 : 1,
        filter: `brightness(${selBoost})`,
      };
    case "outline":
      return {
        background: "transparent",
        color: color.text,
        borderColor: color.border,
        opacity: disabled ? 0.6 : 1,
        filter: `brightness(${selBoost})`,
      };
    case "soft":
    default:
      return {
        background: color.soft,
        color: color.text,
        borderColor: color.border,
        opacity: disabled ? 0.6 : 1,
        filter: `brightness(${selBoost})`,
      };
  }
}

const iconWrap: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 14,
  height: 14,
};

const closeBtnStyleBase: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 16,
  height: 16,
  borderRadius: 999,
  marginLeft: 2,
  border: "none",
  background: "transparent",
  cursor: "pointer",
  padding: 0,
  lineHeight: 0,
};

function closeBtnHover(bg: string): CSSProperties {
  return { background: bg, filter: "brightness(0.98)" };
}

export function Tag<T extends "span" | "button" | "a">(props: TagProps<T>) {
  const {
    as,
    children,
    color = "neutral",
    variant = "soft",
    size = "md",
    selected = false,
    pill = false,
    leadingIcon,
    trailingIcon,
    removable = false,
    onRemove,
    onToggle,
    className,
    style,
    title,
    disabled,
    href,
    target,
    rel,
    type,
    ...rest
  } = props as TagProps<any>;

  const interactive = Boolean(onToggle || href);
  const TagEl = (as ?? (onToggle ? "button" : href ? "a" : "span")) as any;
  const pal = palette[color] ?? palette.neutral;

  const base = baseStyle(size, pill, interactive);
  const byVariant = variantStyle(variant, pal, selected, disabled);

  const merged: CSSProperties = {
    ...base,
    ...byVariant,
    ...(style || {}),
  };

  const handleToggle = useCallback(() => {
    if (onToggle && !disabled) onToggle(!selected);
  }, [onToggle, selected, disabled]);

  const handleKeyDown: React.KeyboardEventHandler<HTMLElement> = (e) => {
    if (!onToggle || disabled) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle(!selected);
    }
  };

  const closeTitle = typeof children === "string" ? `Remove ${children}` : "Remove tag";

  const commonProps: any = {
    className,
    style: merged,
    title,
    "aria-disabled": disabled || undefined,
    ...rest,
  };

  if (TagEl === "a") {
    commonProps.href = href;
    if (target) commonProps.target = target;
    if (rel) commonProps.rel = rel;
  }
  if (TagEl === "button") {
    commonProps.type = type ?? "button";
    commonProps.disabled = disabled;
  }

  if (onToggle) {
    commonProps.onClick = (e: React.MouseEvent<HTMLElement>) => {
      if (disabled) return;
      handleToggle();
      (rest as any).onClick?.(e);
    };
    commonProps.onKeyDown = (e: React.KeyboardEvent<HTMLElement>) => {
      handleKeyDown(e);
      (rest as any).onKeyDown?.(e);
    };
    commonProps["aria-pressed"] = selected;
    commonProps.role = commonProps.role ?? "button";
  }

  return (
    <TagEl {...commonProps}>
      {leadingIcon ? <span aria-hidden="true" style={iconWrap}>{leadingIcon}</span> : null}
      <span>{children}</span>
      {trailingIcon ? <span aria-hidden="true" style={iconWrap}>{trailingIcon}</span> : null}
      {removable ? (
        <button
          type="button"
          aria-label={closeTitle}
          title={closeTitle}
          onClick={onRemove}
          onKeyDown={(e) => {
            if (e.key === "Backspace" || e.key === "Delete") {
              onRemove?.(e);
            }
          }}
          style={{
            ...closeBtnStyleBase,
            color: pal.text,
          }}
          onMouseEnter={(e) => {
            Object.assign((e.currentTarget as HTMLButtonElement).style, closeBtnHover(pal.soft));
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = "transparent";
            (e.currentTarget as HTMLButtonElement).style.filter = "";
          }}
        >
          <svg width="10" height="10" viewBox="0 0 12 12" aria-hidden="true">
            <path
              d="M3 3l6 6M9 3L3 9"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
            />
          </svg>
        </button>
      ) : null}
    </TagEl>
  );
}

export namespace Tag {
  export const Group: React.FC<
    {
      gap?: number;
      wrap?: boolean;
      className?: string;
      style?: CSSProperties;
      children?: ReactNode;
    }
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

export default Tag;
