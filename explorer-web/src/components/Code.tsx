import React from "react";
import cn from "../utils/classnames";

export interface CodeProps {
  /** If provided, used both for rendering and copying. Otherwise children textContent is used. */
  value?: string;
  /** Optional language label shown in the corner (purely cosmetic). */
  language?: string;
  /** If true, long lines will wrap; otherwise enable horizontal scroll. */
  wrap?: boolean;
  /** Optional custom class for the outer wrapper. */
  className?: string;
  /** Max height for the code block (px). Overflow will scroll. */
  maxHeight?: number;
  /** Accessible label for the copy button. */
  copyLabel?: string;
  /** Called after a successful copy. */
  onCopied?: () => void;
  /** Code content; ignored if `value` is given. */
  children?: React.ReactNode;
}

/**
 * Code — monospaced block with a built-in copy button and optional language label.
 * Self-contained styles; no global CSS required.
 */
export default function Code({
  value,
  language,
  wrap = false,
  className,
  maxHeight,
  copyLabel = "Copy to clipboard",
  onCopied,
  children,
}: CodeProps) {
  const codeRef = React.useRef<HTMLElement | null>(null);
  const [copied, setCopied] = React.useState(false);
  const resetTimer = React.useRef<number | null>(null);

  React.useEffect(() => () => resetTimer.current && window.clearTimeout(resetTimer.current), []);

  const getTextToCopy = (): string => {
    if (typeof value === "string") return value;
    const el = codeRef.current;
    if (!el) return "";
    // innerText preserves line breaks as rendered
    return el.innerText || "";
  };

  const doCopy = async () => {
    const text = getTextToCopy();
    if (!text) return;
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback: create a temporary textarea
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.opacity = "0";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
      }
      setCopied(true);
      onCopied?.();
      if (resetTimer.current) window.clearTimeout(resetTimer.current);
      resetTimer.current = window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // swallow; no-op
    }
  };

  const content = typeof value === "string" ? value : children;

  return (
    <div
      className={cn("code-wrap", className)}
      style={{
        position: "relative",
      }}
    >
      <div className="code-toolbar">
        {language ? <span className="code-lang" title="Language">{language}</span> : <span />}
        <button
          type="button"
          className="code-copy"
          aria-live="polite"
          aria-label={copyLabel}
          onClick={doCopy}
        >
          {copied ? (
            <span className="copied">
              <CheckIcon />
              <span className="sr-only">Copied</span>
            </span>
          ) : (
            <span className="copy">
              <CopyIcon />
              <span className="sr-only">Copy</span>
            </span>
          )}
        </button>
      </div>
      <pre
        className="code-pre"
        style={{
          overflowX: wrap ? "hidden" : "auto",
          whiteSpace: wrap ? "pre-wrap" as const : "pre" as const,
          maxHeight: typeof maxHeight === "number" ? maxHeight : undefined,
        }}
      >
        <code ref={codeRef} className="code-code">
          {content}
        </code>
      </pre>
      <style>{styles}</style>
    </div>
  );
}

function CopyIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M8 8a2 2 0 012-2h8a2 2 0 012 2v9a2 2 0 01-2 2h-8a2 2 0 01-2-2V8z"
        fill="currentColor"
        opacity="0.6"
      />
      <path
        d="M6 6a2 2 0 012-2h7a1 1 0 010 2H8v10a1 1 0 01-2 0V6z"
        fill="currentColor"
      />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d="M9 16.2l-3.5-3.5a1 1 0 10-1.4 1.4l4.2 4.2a1 1 0 001.4 0l10-10a1 1 0 10-1.4-1.4L9 16.2z"
        fill="currentColor"
      />
    </svg>
  );
}

const styles = `
/* Visually hidden but screen-reader accessible */
.sr-only {
  position: absolute;
  width: 1px; height: 1px;
  margin: -1px; border: 0; padding: 0;
  white-space: nowrap;
  clip-path: inset(50%);
  clip: rect(0 0 0 0);
  overflow: hidden;
}

/* Wrapper */
.code-wrap {
  --code-bg: rgba(17, 24, 39, 0.92); /* ~#111827 */
  --code-fg: #e5e7eb; /* gray-200 */
  --code-border: rgba(255,255,255,0.08);
  --code-muted: rgba(229,231,235,0.65);
  --code-accent: #93c5fd; /* sky-300 */
  border: 1px solid var(--code-border);
  border-radius: 10px;
  background: var(--code-bg);
  color: var(--code-fg);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
}

/* Toolbar */
.code-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 8px;
  border-bottom: 1px solid var(--code-border);
}

.code-lang {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--code-muted);
}

.code-copy {
  appearance: none;
  border: none;
  background: transparent;
  color: var(--code-muted);
  padding: 4px;
  border-radius: 6px;
  cursor: pointer;
  line-height: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.code-copy:hover { color: var(--code-fg); background: rgba(255,255,255,.06); }
.code-copy:active { transform: translateY(0.5px); }
.code-copy:focus-visible { outline: 2px solid var(--code-accent); outline-offset: 2px; }

.code-pre {
  margin: 0;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.5;
  border-bottom-left-radius: 10px;
  border-bottom-right-radius: 10px;
}

.code-code {
  display: block;
  tab-size: 2;
  caret-color: var(--code-fg);
  /* Allow selection for copy via OS gesture too */
  user-select: text;
}

/* Optional syntax-like hints for common tokens (very light) */
.code-code .k { color: #fca5a5; }       /* keyword-ish */
.code-code .s { color: #86efac; }       /* string-ish */
.code-code .n { color: #93c5fd; }       /* number-ish */
`;
