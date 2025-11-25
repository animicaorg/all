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

export interface TagProps {
  children?: React.ReactNode;
  /** Visual style variant. */
  variant?: Variant;
  /** Color family. */
  color?: Color;
  /** Size presets. */
  size?: Size;
  /** Optional leading icon. */
  leadingIcon?: React.ReactNode;
  /** Optional trailing icon. */
  trailingIcon?: React.ReactNode;
  /** If provided, renders a remove button inside the tag. */
  onRemove?: (e: React.MouseEvent | React.KeyboardEvent) => void;
  /** If provided, makes the tag toggle-able (pressed = selected). */
  selected?: boolean;
  onToggle?: (selected: boolean) => void;
  /** Truncate long text with ellipsis (keeps one line). */
  truncate?: boolean;
  /** Uppercase text (useful for labels). */
  uppercase?: boolean;
  /** Disabled state (locks toggle & remove). */
  disabled?: boolean;
  /** Optional click handler (renders as <button>). */
  onClick?: React.MouseEventHandler<HTMLButtonElement>;
  /** Optional href (renders as <a>). */
  href?: string;
  /** Title/tooltip text. */
  title?: string;
  /** Extra class name for outer element. */
  className?: string;
  /** data-testid for testing. */
  "data-testid"?: string;
}

/** Color palette per variant, provided as CSS custom props. */
function palette(variant: Variant, color: Color, selected?: boolean): React.CSSProperties {
  const c = {
    gray:  { base: "#6b7280", strong: "#374151", lightBg: "#f3f4f6", lightBd: "#e5e7eb", midBd: "#d1d5db" },
    blue:  { base: "#2563eb", strong: "#1e40af", lightBg: "#eff6ff", lightBd: "#bfdbfe", midBd: "#93c5fd" },
    green: { base: "#10b981", strong: "#065f46", lightBg: "#ecfdf5", lightBd: "#a7f3d0", midBd: "#6ee7b7" },
    red:   { base: "#ef4444", strong: "#991b1b", lightBg: "#fef2f2", lightBd: "#fecaca", midBd: "#fca5a5" },
    yellow:{ base: "#f59e0b", strong: "#92400e", lightBg: "#fffbeb", lightBd: "#fde68a", midBd: "#fcd34d" },
    purple:{ base: "#7c3aed", strong: "#4c1d95", lightBg: "#f5f3ff", lightBd: "#ddd6fe", midBd: "#c4b5fd" },
    indigo:{ base: "#4f46e5", strong: "#3730a3", lightBg: "#eef2ff", lightBd: "#c7d2fe", midBd: "#a5b4fc" },
    orange:{ base: "#f97316", strong: "#7c2d12", lightBg: "#fff7ed", lightBd: "#fed7aa", midBd: "#fdba74" },
  } as const;
  const p = c[color];

  // When selected, intensify background/border subtly for non-solid variants.
  const selBg = variant === "solid" ? p.base : p.midBd + "33"; // translucent
  const selBd = variant === "solid" ? p.base : p.midBd;

  switch (variant) {
    case "solid":
      return {
        ["--t-bg" as any]: selected ? selBg : p.base,
        ["--t-fg" as any]: "#ffffff",
        ["--t-bd" as any]: selected ? selBd : p.base,
        ["--t-fg-muted" as any]: "#ffffff",
      };
    case "soft":
      return {
        ["--t-bg" as any]: selected ? selBg : p.lightBg,
        ["--t-fg" as any]: p.strong,
        ["--t-bd" as any]: selected ? selBd : p.lightBd,
        ["--t-fg-muted" as any]: p.strong,
      };
    case "outline":
      return {
        ["--t-bg" as any]: selected ? selBg : "transparent",
        ["--t-fg" as any]: p.strong,
        ["--t-bd" as any]: selected ? selBd : p.midBd,
        ["--t-fg-muted" as any]: p.strong,
      };
    case "minimal":
    default:
      return {
        ["--t-bg" as any]: selected ? selBg : "transparent",
        ["--t-fg" as any]: p.strong,
        ["--t-bd" as any]: selected ? selBd : "transparent",
        ["--t-fg-muted" as any]: p.strong,
      };
  }
}

function sizeVars(size: Size): React.CSSProperties {
  switch (size) {
    case "sm":
      return {
        ["--t-pad-y" as any]: "2px",
        ["--t-pad-x" as any]: "8px",
        ["--t-gap" as any]: "6px",
        ["--t-fz" as any]: "12px",
        ["--t-ico" as any]: "12px",
        ["--t-x" as any]: "14px",
        ["--t-radius" as any]: "8px",
      };
    case "lg":
      return {
        ["--t-pad-y" as any]: "6px",
        ["--t-pad-x" as any]: "12px",
        ["--t-gap" as any]: "8px",
        ["--t-fz" as any]: "14px",
        ["--t-ico" as any]: "16px",
        ["--t-x" as any]: "18px",
        ["--t-radius" as any]: "10px",
      };
    case "md":
    default:
      return {
        ["--t-pad-y" as any]: "4px",
        ["--t-pad-x" as any]: "10px",
        ["--t-gap" as any]: "8px",
        ["--t-fz" as any]: "12px",
        ["--t-ico" as any]: "14px",
        ["--t-x" as any]: "16px",
        ["--t-radius" as any]: "9px",
      };
  }
}

/**
 * Tag — compact, optionally removable and/or toggle-able label.
 * Keyboard:
 *  - Space/Enter toggles when toggle-able.
 *  - Delete/Backspace triggers remove when removable.
 */
export function Tag({
  children,
  variant = "soft",
  color = "gray",
  size = "md",
  leadingIcon,
  trailingIcon,
  onRemove,
  selected,
  onToggle,
  truncate,
  uppercase,
  disabled,
  onClick,
  href,
  title,
  className,
  "data-testid": testid,
}: TagProps) {
  const style = React.useMemo(
    () => ({ ...palette(variant, color, selected), ...sizeVars(size) }),
    [variant, color, size, selected]
  );

  const isToggleable = typeof selected === "boolean" && typeof onToggle === "function";
  const isInteractive = !!onClick || !!href || isToggleable;

  const classes = [
    "tag",
    `v-${variant}`,
    `c-${color}`,
    `s-${size}`,
    isInteractive ? "is-interactive" : "",
    disabled ? "is-disabled" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (disabled) return;
    if (isToggleable && (e.key === " " || e.key === "Enter")) {
      e.preventDefault();
      onToggle?.(!selected);
    } else if (onRemove && (e.key === "Backspace" || e.key === "Delete")) {
      e.preventDefault();
      onRemove(e);
    }
  };

  const content = (
    <>
      {leadingIcon && <span className="t-ico" aria-hidden="true">{leadingIcon}</span>}
      <span className={`t-text ${truncate ? "truncate" : ""}`}>
        {uppercase ? String(children ?? "").toUpperCase() : children}
      </span>
      {trailingIcon && <span className="t-ico" aria-hidden="true">{trailingIcon}</span>}
      {onRemove && (
        <button
          type="button"
          className="t-xbtn"
          aria-label="Remove tag"
          title="Remove"
          onClick={(e) => !disabled && onRemove(e)}
          disabled={disabled}
          tabIndex={-1}
        >
          <CloseIcon />
        </button>
      )}
      <style>{css}</style>
    </>
  );

  const commonProps = {
    className: classes,
    style,
    title,
    "data-testid": testid,
  } as const;

  if (href) {
    return (
      <a {...commonProps} href={href} onKeyDown={onKeyDown} aria-disabled={disabled || undefined}>
        {content}
      </a>
    );
  }

  if (onClick || isToggleable) {
    return (
      <button
        type="button"
        {...commonProps}
        onClick={(e) => {
          if (disabled) return;
          onClick?.(e);
          if (isToggleable && e.defaultPrevented === false) onToggle?.(!selected);
        }}
        onKeyDown={onKeyDown}
        aria-pressed={isToggleable ? !!selected : undefined}
        disabled={disabled}
      >
        {content}
      </button>
    );
  }

  return (
    <span {...commonProps} onKeyDown={onKeyDown} role={isToggleable ? "button" : undefined} tabIndex={isToggleable ? 0 : undefined} aria-pressed={isToggleable ? !!selected : undefined}>
      {content}
    </span>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5.3 5.3a1 1 0 0 1 1.4 0L10 8.6l3.3-3.3a1 1 0 1 1 1.4 1.4L11.4 10l3.3 3.3a1 1 0 0 1-1.4 1.4L10 11.4l-3.3 3.3a1 1 0 0 1-1.4-1.4L8.6 10 5.3 6.7a1 1 0 0 1 0-1.4z" fill="currentColor"/>
    </svg>
  );
}

const css = `
.tag {
  --t-bg: transparent;
  --t-fg: #374151;
  --t-fg-muted: #6b7280;
  --t-bd: transparent;
  --t-pad-y: 4px;
  --t-pad-x: 10px;
  --t-gap: 8px;
  --t-fz: 12px;
  --t-ico: 14px;
  --t-x: 16px;
  --t-radius: 9px;

  display: inline-flex;
  align-items: center;
  gap: var(--t-gap);
  padding: var(--t-pad-y) var(--t-pad-x);
  background: var(--t-bg);
  color: var(--t-fg);
  border: 1px solid var(--t-bd);
  border-radius: var(--t-radius);
  font-weight: 600;
  font-size: var(--t-fz);
  line-height: 1;
  white-space: nowrap;
  user-select: none;
  text-decoration: none; /* for <a> */
}

.tag .t-text {
  display: inline-block;
  max-width: 28ch;
}

.tag .t-text.truncate {
  overflow: hidden;
  text-overflow: ellipsis;
}

.tag .t-ico {
  display: inline-flex;
  width: var(--t-ico);
  height: var(--t-ico);
  line-height: 0;
}

.tag .t-ico > svg {
  width: 100%;
  height: 100%;
}

.tag .t-xbtn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: var(--t-x);
  height: var(--t-x);
  border-radius: 6px;
  border: 1px solid transparent;
  color: var(--t-fg);
  background: transparent;
  cursor: pointer;
  line-height: 0;
  padding: 0;
}

.tag .t-xbtn:hover:not(:disabled) {
  background: rgba(0,0,0,.05);
}

.tag .t-xbtn:active:not(:disabled) {
  transform: translateY(1px);
}

.tag.is-interactive,
button.tag,
a.tag {
  cursor: pointer;
  transition: background .15s ease, border-color .15s ease, transform .02s ease, color .15s ease, box-shadow .15s ease;
}

.tag.is-interactive:hover,
button.tag:hover,
a.tag:hover {
  filter: brightness(0.98);
}

.tag.is-interactive:active,
button.tag:active,
a.tag:active {
  transform: translateY(1px);
}

.tag.is-disabled,
button.tag:disabled,
a.tag[aria-disabled="true"] {
  opacity: .6;
  cursor: not-allowed;
}

/* High-contrast */
@media (prefers-contrast: more) {
  .tag { border-color: var(--t-fg-muted); }
}
`;

export default Tag;

/** Small helpers for common tag styles */
export const TagPreset = {
  info(props: Omit<TagProps, "variant" | "color">) {
    return <Tag variant="soft" color="blue" {...props} />;
  },
  success(props: Omit<TagProps, "variant" | "color">) {
    return <Tag variant="soft" color="green" {...props} />;
  },
  warning(props: Omit<TagProps, "variant" | "color">) {
    return <Tag variant="soft" color="yellow" {...props} />;
  },
  danger(props: Omit<TagProps, "variant" | "color">) {
    return <Tag variant="soft" color="red" {...props} />;
  },
  neutral(props: Omit<TagProps, "variant" | "color">) {
    return <Tag variant="soft" color="gray" {...props} />;
  },
};
