import React, { useEffect, useRef } from "react";
import ReactDOM from "react-dom";

export type ModalProps = {
  open: boolean;
  onClose: () => void;
  title?: string | React.ReactNode;
  description?: string | React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode; // footer actions (buttons)
  size?: "sm" | "md" | "lg";
  dismissible?: boolean; // show top-right X
  closeOnBackdrop?: boolean;
  /** Optional id for aria-labelledby; auto-generated if not provided */
  id?: string;
  className?: string;
  style?: React.CSSProperties;
};

function useLockBody(lock: boolean) {
  useEffect(() => {
    if (!lock) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, [lock]);
}

function useFocusTrap(enabled: boolean, containerRef: React.RefObject<HTMLDivElement>) {
  useEffect(() => {
    if (!enabled || !containerRef.current) return;

    const focusableSelector =
      'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])';
    const node = containerRef.current;

    const focusables = () => Array.from(node.querySelectorAll<HTMLElement>(focusableSelector));

    // Focus first element or container
    const prevActive = document.activeElement as HTMLElement | null;
    const first = focusables()[0] ?? node;
    first.focus();

    function onKeydown(e: KeyboardEvent) {
      if (e.key !== "Tab") return;
      const list = focusables();
      if (list.length === 0) {
        e.preventDefault();
        node.focus();
        return;
      }
      const current = document.activeElement as HTMLElement | null;
      const i = Math.max(0, list.indexOf(current || list[0]));
      const next = (e.shiftKey ? i - 1 + list.length : i + 1) % list.length;
      e.preventDefault();
      list[next].focus();
    }

    document.addEventListener("keydown", onKeydown);
    return () => {
      document.removeEventListener("keydown", onKeydown);
      // restore focus
      prevActive?.focus?.();
    };
  }, [enabled, containerRef]);
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "color-mix(in oklab, var(--backdrop, #0b1220) 68%, transparent)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 9999,
  padding: "24px",
};

const sheetBase: React.CSSProperties = {
  background: "var(--surface-1, #0f172a)",
  color: "var(--text-1, #e5e7eb)",
  borderRadius: 12,
  border: "1px solid var(--border-1, #334155)",
  boxShadow:
    "0 10px 30px rgba(0,0,0,.45), 0 0 0 1px color-mix(in oklab, var(--border-1, #334155) 30%, transparent)",
  maxHeight: "min(86vh, 900px)",
  display: "grid",
  gridTemplateRows: "auto 1fr auto",
  outline: "none",
};

const sizeMap: Record<Required<ModalProps>["size"], React.CSSProperties> = {
  sm: { width: "min(420px, 100%)" },
  md: { width: "min(640px, 100%)" },
  lg: { width: "min(860px, 100%)" },
};

const headerStyle: React.CSSProperties = {
  padding: "16px 18px",
  borderBottom: "1px solid var(--border-1, #334155)",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 12,
};

const titleStyle: React.CSSProperties = { fontSize: 16, fontWeight: 700, lineHeight: "20px" };
const descStyle: React.CSSProperties = { marginTop: 6, fontSize: 13, opacity: 0.85 };

const bodyStyle: React.CSSProperties = {
  padding: 18,
  overflow: "auto",
};

const footerStyle: React.CSSProperties = {
  padding: 14,
  borderTop: "1px solid var(--border-1, #334155)",
  display: "flex",
  alignItems: "center",
  justifyContent: "flex-end",
  gap: 10,
};

const closeBtnStyle: React.CSSProperties = {
  appearance: "none",
  background: "transparent",
  color: "var(--text-2, #cbd5e1)",
  border: "none",
  padding: 6,
  borderRadius: 8,
  cursor: "pointer",
};

const iconX = (
  <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
    <path
      d="M6 6l12 12M18 6L6 18"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  </svg>
);

/**
 * Modal — accessible modal dialog component.
 * - Focus trap + body scroll lock when open
 * - ESC to close
 * - Backdrop click to close (opt-in via closeOnBackdrop)
 */
export default function Modal({
  open,
  onClose,
  title,
  description,
  children,
  actions,
  size = "md",
  dismissible = true,
  closeOnBackdrop = true,
  id,
  className,
  style,
}: ModalProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  useLockBody(open);
  useFocusTrap(open, sheetRef);

  // ESC handler
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    }
    document.addEventListener("keydown", onKey, { capture: true });
    return () => document.removeEventListener("keydown", onKey, { capture: true } as any);
  }, [open, onClose]);

  if (!open) return null;

  const labelledId = id ? `${id}-title` : "modal-title";
  const describedId = id ? `${id}-desc` : description ? "modal-desc" : undefined;

  const sheetProps = {
    role: "dialog",
    "aria-modal": true,
    "aria-labelledby": labelledId,
    "aria-describedby": describedId,
    tabIndex: -1,
  } as const;

  const sheet = (
    <div
      style={overlayStyle}
      onMouseDown={(e) => {
        if (closeOnBackdrop && e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={sheetRef}
        {...sheetProps}
        className={className}
        style={{ ...sheetBase, ...sizeMap[size], ...(style || {}) }}
      >
        {(title || dismissible) && (
          <div style={headerStyle}>
            <div style={{ display: "grid" }}>
              {title ? (
                <div id={labelledId} style={titleStyle}>
                  {title}
                </div>
              ) : (
                <span id={labelledId} style={{ position: "absolute", inset: -9999 }}>Dialog</span>
              )}
              {description ? (
                <div id={describedId} style={descStyle}>
                  {description}
                </div>
              ) : null}
            </div>
            {dismissible && (
              <button
                type="button"
                onClick={onClose}
                aria-label="Close dialog"
                title="Close"
                style={closeBtnStyle}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--surface-2, #0b1220)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                {iconX}
              </button>
            )}
          </div>
        )}
        <div style={bodyStyle}>{children}</div>
        {actions ? <div style={footerStyle}>{actions}</div> : null}
      </div>
    </div>
  );

  // MV3 pages run as standalone docs; portal to body is fine.
  return ReactDOM.createPortal(sheet, document.body);
}
