import React, { useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { useStore } from "../state/store";

/**
 * ToastHost
 * ----------
 * Global toasts container. Mount once near the root (e.g., in App.tsx).
 *
 * Expects a toasts slice roughly as:
 *   state.toasts = {
 *     items: Array<{ id: string; kind?: 'success'|'error'|'info'|'warning'; title?: string; message: string; timeoutMs?: number }>,
 *     remove: (id: string) => void,
 *     clear: () => void
 *   }
 *
 * Styling relies on CSS variables defined in the app theme:
 *   --surface-elev-2, --fg, --fg-inverse, --border, --success, --danger, --warning, --accent
 */

type ToastKind = "success" | "error" | "warning" | "info" | undefined;

function kindIcon(kind: ToastKind) {
  switch (kind) {
    case "success":
      return "✓";
    case "error":
      return "⨯";
    case "warning":
      return "!";
    case "info":
    default:
      return "ℹ";
  }
}

function kindColor(kind: ToastKind) {
  switch (kind) {
    case "success":
      return "var(--success)";
    case "error":
      return "var(--danger)";
    case "warning":
      return "var(--warning)";
    case "info":
    default:
      return "var(--accent)";
  }
}

function roleFor(kind: ToastKind): "status" | "alert" {
  return kind === "error" || kind === "warning" ? "alert" : "status";
}

const ToastItem: React.FC<{
  id: string;
  kind?: ToastKind;
  title?: string;
  message: string;
  timeoutMs?: number;
  onClose: (id: string) => void;
}> = ({ id, kind, title, message, timeoutMs = 5000, onClose }) => {
  const timerRef = useRef<number | null>(null);
  const reducedMotion = useMemo(
    () =>
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    []
  );

  useEffect(() => {
    // auto-dismiss
    if (timeoutMs! > 0) {
      timerRef.current = window.setTimeout(() => onClose(id), timeoutMs);
    }
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [id, timeoutMs, onClose]);

  return (
    <div
      className="toast"
      role={roleFor(kind)}
      aria-live="polite"
      aria-atomic="true"
      data-kind={kind || "info"}
      style={
        {
          "--kind": kindColor(kind) as string,
          "--enter-ms": reducedMotion ? "0ms" : "160ms",
          "--leave-ms": reducedMotion ? "0ms" : "160ms",
        } as React.CSSProperties
      }
    >
      <div className="bar" aria-hidden="true" />
      <div className="icon" aria-hidden="true">
        {kindIcon(kind)}
      </div>
      <div className="content">
        {title && <div className="title">{title}</div>}
        <div className="message">{message}</div>
      </div>
      <button
        className="close"
        aria-label="Dismiss notification"
        onClick={() => onClose(id)}
        title="Dismiss"
      >
        ✕
      </button>
      <style>{`
        .toast {
          display: grid;
          grid-template-columns: 4px 24px 1fr auto;
          align-items: start;
          gap: 10px;
          width: min(560px, 92vw);
          background: var(--surface-elev-2, #111);
          color: var(--fg, #eaeaea);
          border: 1px solid var(--border, #2a2a2a);
          border-radius: 12px;
          box-shadow: 0 8px 24px rgba(0, 0, 0, 0.28);
          padding: 10px 12px 10px 0;
          overflow: hidden;
          transform: translate3d(0, 8px, 0);
          opacity: 0;
          animation: toast-in var(--enter-ms) ease-out forwards;
        }
        .bar {
          width: 4px;
          height: 100%;
          background: color-mix(in oklab, var(--kind) 88%, white 0%);
        }
        .icon {
          width: 24px;
          height: 24px;
          margin-top: 2px;
          display: grid;
          place-items: center;
          border-radius: 999px;
          font-weight: 800;
          font-size: 13px;
          color: var(--fg-inverse, #0b0b0b);
          background: var(--kind);
          box-shadow: 0 0 0 3px color-mix(in oklab, var(--kind) 25%, transparent);
        }
        .content {
          display: grid;
          gap: 2px;
          min-width: 0;
        }
        .title {
          font-weight: 700;
          letter-spacing: 0.01em;
          line-height: 1.2;
        }
        .message {
          color: var(--fg-muted, #c9c9c9);
          line-height: 1.4;
          overflow-wrap: anywhere;
        }
        .close {
          appearance: none;
          border: 0;
          background: transparent;
          color: var(--fg-muted, #c9c9c9);
          font-size: 16px;
          line-height: 1;
          padding: 4px 6px;
          margin: -4px -2px 0 0;
          border-radius: 8px;
          cursor: pointer;
        }
        .close:hover {
          color: var(--fg, #eaeaea);
          background: color-mix(in oklab, var(--fg) 8%, transparent);
        }

        @keyframes toast-in {
          from {
            transform: translate3d(0, 8px, 0);
            opacity: 0;
          }
          to {
            transform: translate3d(0, 0, 0);
            opacity: 1;
          }
        }
      `}</style>
    </div>
  );
};

export default function ToastHost() {
  const items = useStore((s: any) => s.toasts?.items ?? []);
  const remove = useStore((s: any) => s.toasts?.remove ?? (() => {}));

  // Create a portal root
  const rootEl = useMemo(() => {
    if (typeof document === "undefined") return null;
    const id = "toast-root";
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      document.body.appendChild(el);
    }
    return el;
  }, []);

  if (!rootEl) return null;

  return createPortal(
    <>
      <div className="toast-host" aria-live="polite" aria-relevant="additions text">
        {items.map((t: any) => (
          <ToastItem
            key={t.id}
            id={String(t.id)}
            kind={t.kind as ToastKind}
            title={t.title}
            message={t.message}
            timeoutMs={t.timeoutMs}
            onClose={remove}
          />
        ))}
      </div>
      <style>{`
        .toast-host {
          position: fixed;
          right: 16px;
          bottom: 16px;
          display: grid;
          gap: 10px;
          z-index: 1000;
          pointer-events: none; /* allow clicks to pass between toasts */
        }
        .toast-host .toast {
          pointer-events: auto; /* re-enable on individual toast */
        }

        @media (max-width: 640px) {
          .toast-host {
            left: 50%;
            right: auto;
            bottom: 12px;
            transform: translateX(-50%);
            width: 100%;
            max-width: 96vw;
            padding: 0 8px;
          }
        }
      `}</style>
    </>,
    rootEl
  );
}
