import React from "react";

export type CopyProps = {
  /** The value to copy. Can be a string or a thunk that returns the latest string. */
  value: string | (() => string);
  /** Visual variant. 'icon' shows only the icon, 'button' shows a label, 'inline' shows value + icon. */
  variant?: "icon" | "button" | "inline";
  /** How to show the value for 'inline' variant. */
  show?: "truncate" | "value" | "none";
  /** Middle-truncation lengths (head/tail) when show="truncate". */
  truncateHead?: number;
  truncateTail?: number;
  /** Accessible label for the button (overrides default). */
  label?: string;
  /** Text shown on the button for variant="button" (default: "Copy"). */
  buttonText?: string;
  /** Text announced after a successful copy (aria-live) & shown briefly as UI state. */
  copiedText?: string;
  /** Milliseconds before the "copied" state resets. */
  resetMs?: number;
  /** Optional title tooltip. If not provided, a sensible default is set. */
  title?: string;
  /** Class name for outer wrapper. */
  className?: string;
  /** Called after a copy attempt with the result. */
  onCopy?: (ok: boolean) => void;
  /** Optional children to fully control inner content (icon still toggles on success). */
  children?: React.ReactNode;
  /** If true, renders a small, compact control. */
  small?: boolean;
  /** data-testid for testing. */
  "data-testid"?: string;
};

function getValue(v: CopyProps["value"]): string {
  try {
    return typeof v === "function" ? (v as () => string)() : v;
  } catch {
    return "";
  }
}

function middleTruncate(s: string, head = 6, tail = 4): string {
  if (s.length <= head + tail + 1) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // fall through to legacy path
  }

  // Fallback: hidden textarea + execCommand
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    // Prevent keyboard from popping on iOS
    ta.readOnly = true;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    ta.style.top = "0";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    ta.setSelectionRange(0, ta.value.length);
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

export default function Copy({
  value,
  variant = "icon",
  show = "truncate",
  truncateHead = 6,
  truncateTail = 4,
  label,
  buttonText,
  copiedText,
  resetMs = 1600,
  title,
  className,
  onCopy,
  children,
  small,
  "data-testid": testid,
}: CopyProps) {
  const [copied, setCopied] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);
  const liveRef = React.useRef<HTMLSpanElement>(null);
  const timerRef = React.useRef<number | null>(null);

  const text = React.useMemo(() => getValue(value), [value]);
  const display =
    show === "none"
      ? ""
      : show === "value"
      ? text
      : middleTruncate(text, truncateHead, truncateTail);

  const ariaLabel =
    label ??
    (variant === "button"
      ? "Copy to clipboard"
      : `Copy value to clipboard`);

  const successMsg = copiedText ?? "Copied to clipboard";
  const tooltip =
    title ??
    (text ? `Copy "${display === text ? display : text}"` : "Copy value");

  React.useEffect(() => {
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, []);

  const doCopy = async () => {
    setErr(null);
    const ok = await copyToClipboard(text);
    onCopy?.(ok);
    if (ok) {
      setCopied(true);
      // announce to AT
      if (liveRef.current) {
        liveRef.current.textContent = successMsg;
        // clear after a tick so SR can re-announce on subsequent copies
        window.setTimeout(() => {
          if (liveRef.current) liveRef.current.textContent = "";
        }, resetMs + 100);
      }
      if (timerRef.current) window.clearTimeout(timerRef.current);
      timerRef.current = window.setTimeout(() => setCopied(false), resetMs) as any;
    } else {
      setErr("Copy failed");
    }
  };

  const classes = [
    "copy",
    `v-${variant}`,
    copied ? "is-copied" : "",
    small ? "is-small" : "",
    className || "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <span className={classes} data-testid={testid}>
      {/* aria-live region for screen readers */}
      <span ref={liveRef} className="sr-only" aria-live="polite" />

      {variant === "inline" && show !== "none" && (
        <span className="copy-val" title={text}>
          <code>{display}</code>
        </span>
      )}

      <button
        type="button"
        className="copy-btn"
        onClick={doCopy}
        aria-label={ariaLabel}
        title={tooltip}
      >
        {children ? (
          <>
            {children}
            <Icon state={copied ? "check" : "copy"} />
          </>
        ) : variant === "button" ? (
          <>
            <Icon state={copied ? "check" : "copy"} />
            <span className="btn-label">
              {copied ? successMsg : buttonText ?? "Copy"}
            </span>
          </>
        ) : (
          <Icon state={copied ? "check" : "copy"} />
        )}
      </button>

      {err && <span className="copy-err" role="alert">{err}</span>}

      <style>{css}</style>
    </span>
  );
}

function Icon({ state }: { state: "copy" | "check" }) {
  if (state === "check") {
    return (
      <svg
        className="icon"
        width="16"
        height="16"
        viewBox="0 0 24 24"
        aria-hidden="true"
      >
        <path
          fill="currentColor"
          d="M9 16.2l-3.5-3.5L4 14.2 9 19l11-11-1.4-1.4z"
        />
      </svg>
    );
  }
  return (
    <svg
      className="icon"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path
        fill="currentColor"
        d="M16 1H6a2 2 0 0 0-2 2v12h2V3h10V1zm3 4H10a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2zm0 16H10V7h9v14z"
      />
    </svg>
  );
}

const css = `
.copy {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: inherit;
  color: var(--text, #111827);
}

.copy .sr-only {
  position: absolute;
  left: -9999px;
  width: 1px; height: 1px;
  overflow: hidden;
}

.copy .copy-val code {
  padding: 2px 6px;
  border-radius: 6px;
  background: var(--bg-elev-1, #f3f4f6);
  border: 1px solid var(--border-muted, #e5e7eb);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12px;
  color: var(--text, #111827);
}

.copy .copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--border-muted, #e5e7eb);
  background: var(--bg-elev-0, #fff);
  color: var(--text, #111827);
  padding: 6px 8px;
  border-radius: 8px;
  cursor: pointer;
  transition: transform .02s ease, background .15s ease;
}

.copy.v-icon .copy-btn {
  padding: 6px;
}

.copy .copy-btn:hover {
  background: var(--bg-elev-1, #f3f4f6);
}

.copy .copy-btn:active {
  transform: translateY(1px);
}

.copy.is-copied .copy-btn {
  border-color: #10b981;
  background: #ecfdf5;
  color: #065f46;
}

.copy.is-small .copy-btn {
  padding: 4px 6px;
  border-radius: 6px;
}

.copy .icon {
  display: inline-block;
}

.copy .btn-label {
  font-weight: 600;
  font-size: 12px;
}

.copy .copy-err {
  color: #ef4444;
  font-size: 12px;
}
`;
