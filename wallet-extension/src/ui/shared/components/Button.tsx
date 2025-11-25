import React, { forwardRef } from "react";

type Variant = "primary" | "secondary" | "danger" | "ghost";
type Size = "sm" | "md" | "lg";

type CommonProps = {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  iconLeft?: React.ReactNode;
  iconRight?: React.ReactNode;
  fullWidth?: boolean;
  /** Pass extra inline styles if needed */
  style?: React.CSSProperties;
  className?: string;
};

type ButtonAsButton = CommonProps &
  Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "style" | "className"> & {
    as?: "button";
  };

type ButtonAsLink = CommonProps &
  Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, "style" | "className"> & {
    as: "a";
    href: string;
  };

export type ButtonProps = ButtonAsButton | ButtonAsLink;

function cx(...parts: Array<string | undefined | false>) {
  return parts.filter(Boolean).join(" ");
}

function styles(variant: Variant, size: Size, fullWidth?: boolean): React.CSSProperties {
  const base: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    borderRadius: 10,
    borderWidth: 1,
    borderStyle: "solid",
    fontWeight: 600,
    userSelect: "none",
    cursor: "pointer",
    transition: "background-color .16s ease, border-color .16s ease, box-shadow .16s ease, transform .02s ease",
    width: fullWidth ? "100%" : undefined,
    outline: "none",
  };

  const sizeMap: Record<Size, React.CSSProperties> = {
    sm: { padding: "6px 10px", fontSize: 12, lineHeight: "16px" },
    md: { padding: "10px 14px", fontSize: 14, lineHeight: "18px" },
    lg: { padding: "14px 18px", fontSize: 16, lineHeight: "20px" },
  };

  const v: Record<Variant, React.CSSProperties> = {
    primary: {
      background: "var(--accent-9, #3b82f6)",
      color: "var(--text-on-accent, #fff)",
      borderColor: "var(--accent-10, #1d4ed8)",
    },
    secondary: {
      background: "var(--surface-2, #0f172a)",
      color: "var(--text-1, #e5e7eb)",
      borderColor: "var(--border-1, #334155)",
    },
    danger: {
      background: "var(--red-9, #ef4444)",
      color: "var(--text-on-danger, #fff)",
      borderColor: "var(--red-10, #b91c1c)",
    },
    ghost: {
      background: "transparent",
      color: "var(--text-1, #e5e7eb)",
      borderColor: "var(--border-1, #334155)",
    },
  };

  return { ...base, ...sizeMap[size], ...v[variant] };
}

const focusRing: React.CSSProperties = {
  boxShadow: "0 0 0 3px var(--focus-ring, rgba(59,130,246,.35))",
};

const disabledStyles: React.CSSProperties = {
  opacity: 0.5,
  cursor: "not-allowed",
};

const Spinner = ({ size = 14 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    role="img"
    aria-label="loading"
    style={{ animation: "animica-spin 0.9s linear infinite" }}
  >
    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" fill="none" opacity="0.25" />
    <path d="M22 12a10 10 0 0 1-10 10" stroke="currentColor" strokeWidth="3" fill="none" />
    <style>{`@keyframes animica-spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}`}</style>
  </svg>
);

/**
 * Button — accessible, MV3-friendly button with variants & loading state.
 * Uses CSS variables from theme.css when available, but also has sane fallbacks.
 */
const Button = forwardRef<HTMLButtonElement | HTMLAnchorElement, ButtonProps>(function Button(
  props,
  ref
) {
  const {
    as = "button",
    variant = "primary",
    size = "md",
    loading = false,
    iconLeft,
    iconRight,
    fullWidth,
    style,
    className,
    ...rest
  } = props as ButtonProps & { as?: "button" | "a" };

  const isDisabled =
    (as === "button" && (rest as React.ButtonHTMLAttributes<HTMLButtonElement>).disabled) || false;
  const computed = styles(variant, size, fullWidth);

  const sharedProps = {
    ref: ref as any,
    className: cx("animica-btn", className),
    style: {
      ...computed,
      ...(isDisabled || loading ? disabledStyles : null),
      ...(style || {}),
    } as React.CSSProperties,
    onFocus: (e: React.FocusEvent<HTMLElement>) => {
      (e.currentTarget.style.boxShadow as any) = focusRing.boxShadow!;
      (props as any).onFocus?.(e);
    },
    onBlur: (e: React.FocusEvent<HTMLElement>) => {
      (e.currentTarget.style.boxShadow as any) = "none";
      (props as any).onBlur?.(e);
    },
    "aria-disabled": isDisabled || loading ? true : undefined,
    "aria-busy": loading ? true : undefined,
  };

  // Content
  const content = (
    <>
      {loading ? <Spinner size={size === "lg" ? 18 : size === "sm" ? 12 : 14} /> : iconLeft}
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>{(rest as any).children}</span>
      {iconRight}
    </>
  );

  if (as === "a") {
    const linkProps = rest as React.AnchorHTMLAttributes<HTMLAnchorElement>;
    return (
      <a role="button" tabIndex={0} {...sharedProps} {...linkProps}>
        {content}
      </a>
    );
  }

  const btnProps = rest as React.ButtonHTMLAttributes<HTMLButtonElement>;
  return (
    <button type={btnProps.type ?? "button"} disabled={isDisabled || loading} {...sharedProps} {...btnProps}>
      {content}
    </button>
  );
});

export default Button;
