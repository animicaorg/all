import React, { useEffect, useId, useState } from "react";

type CopyProps = {
  /** Text to copy. If a function is provided it will be called at click-time. */
  value: string | (() => string);
  /** Render children next to the icon (e.g., the thing being copied or a label). */
  children?: React.ReactNode;
  /** Visual style: 'icon' renders an icon-only button; 'inline' shows children + icon. */
  variant?: "icon" | "inline";
  /** Size of the icon/button. */
  size?: "sm" | "md";
  /** Optional title/tooltip for the button. */
  title?: string;
  /** Extra classes for the button root. */
  className?: string;
  /** Called after attempt; receives success flag. */
  onCopied?: (success: boolean) => void;
  /** Message announced to screen readers when copy succeeds. */
  srSuccessText?: string;
};

const styles = {
  rootIcon: (sz: "sm" | "md"): React.CSSProperties => ({
    appearance: "none",
    background: "transparent",
    border: "1px solid var(--border-1, #334155)",
    color: "var(--text-2, #cbd5e1)",
    width: sz === "sm" ? 26 : 32,
    height: sz === "sm" ? 26 : 32,
    borderRadius: 8,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
  }),
  rootInline: {
    appearance: "none",
    background: "transparent",
    border: "none",
    color: "var(--text-2, #cbd5e1)",
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    cursor: "pointer",
    padding: 0,
  } as React.CSSProperties,
  icon: (sz: "sm" | "md"): React.CSSProperties => ({
    width: sz === "sm" ? 14 : 16,
    height: sz === "sm" ? 14 : 16,
    flex: "0 0 auto",
  }),
  copiedBadge: {
    marginLeft: 8,
    fontSize: 12,
    color: "var(--accent, #22d3ee)",
    opacity: 0.95,
  },
};

const IconCopy = ({ style }: { style?: React.CSSProperties }) => (
  <svg viewBox="0 0 24 24" fill="none" style={style} aria-hidden="true">
    <path d="M9 9.5A2.5 2.5 0 0 1 11.5 7h6A2.5 2.5 0 0 1 20 9.5v6A2.5 2.5 0 0 1 17.5 18h-6A2.5 2.5 0 0 1 9 15.5v-6Z" stroke="currentColor" strokeWidth="1.6" />
    <path d="M7 16H6.5A2.5 2.5 0 0 1 4 13.5v-6A2.5 2.5 0 0 1 6.5 5h6c.43 0 .84.11 1.2.3" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
  </svg>
);

const IconCheck = ({ style }: { style?: React.CSSProperties }) => (
  <svg viewBox="0 0 24 24" fill="none" style={style} aria-hidden="true">
    <path d="M5 12.5l4.5 4L19 7.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
  }
  try {
    // Legacy fallback
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/**
 * Copy — small utility to copy text to clipboard with a tiny success pulse.
 * Works in MV3 pages and the extension UI.
 */
export default function Copy({
  value,
  children,
  variant = "icon",
  size = "md",
  title = "Copy to clipboard",
  className,
  onCopied,
  srSuccessText = "Copied to clipboard",
}: CopyProps) {
  const [copied, setCopied] = useState(false);
  const srId = useId();

  useEffect(() => {
    if (!copied) return;
    const t = setTimeout(() => setCopied(false), 1200);
    return () => clearTimeout(t);
  }, [copied]);

  const handleClick = async (e: React.MouseEvent) => {
    e.preventDefault();
    const text = typeof value === "function" ? value() : value;
    const ok = await writeClipboard(text);
    setCopied(ok);
    onCopied?.(ok);
  };

  const commonProps = {
    onClick: handleClick,
    title,
    "aria-describedby": copied ? srId : undefined,
    className,
    onMouseEnter: (ev: React.MouseEvent<HTMLButtonElement>) => {
      const el = ev.currentTarget;
      if (variant === "icon") {
        el.style.background = "var(--surface-2, #0b1220)";
      }
    },
    onMouseLeave: (ev: React.MouseEvent<HTMLButtonElement>) => {
      const el = ev.currentTarget;
      if (variant === "icon") {
        el.style.background = "transparent";
      }
    },
  } as const;

  if (variant === "inline") {
    return (
      <button type="button" style={styles.rootInline} {...commonProps}>
        {children}
        {copied ? (
          <IconCheck style={styles.icon(size)} />
        ) : (
          <IconCopy style={styles.icon(size)} />
        )}
        <span id={srId} role="status" aria-live="polite" style={{ position: "absolute", left: -9999 }}>
          {copied ? srSuccessText : ""}
        </span>
        {copied && <span style={styles.copiedBadge}>Copied</span>}
      </button>
    );
  }

  // icon-only button
  return (
    <button
      type="button"
      style={styles.rootIcon(size)}
      aria-label={title}
      {...commonProps}
    >
      {copied ? (
        <IconCheck style={styles.icon(size)} />
      ) : (
        <IconCopy style={styles.icon(size)} />
      )}
      <span id={srId} role="status" aria-live="polite" style={{ position: "absolute", left: -9999 }}>
        {copied ? srSuccessText : ""}
      </span>
    </button>
  );
}
