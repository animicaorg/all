import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";

type ModalSize = "sm" | "md" | "lg" | "xl" | "fullscreen";

export interface ModalProps {
  isOpen: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode; // buttons/footer content
  size?: ModalSize;
  hideCloseButton?: boolean;
  closeOnBackdrop?: boolean; // default true
  closeOnEsc?: boolean; // default true
  initialFocusRef?: React.RefObject<HTMLElement>;
  ariaLabelledBy?: string;
  ariaDescribedBy?: string;
}

/**
 * Accessible, portal-based modal with:
 * - Focus trap & restore
 * - ESC / backdrop close (configurable)
 * - Body scroll lock
 * - Animations respecting prefers-reduced-motion
 * - Works in SSR (no-op until mounted)
 */
const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  actions,
  size = "md",
  hideCloseButton = false,
  closeOnBackdrop = true,
  closeOnEsc = true,
  initialFocusRef,
  ariaLabelledBy,
  ariaDescribedBy,
}) => {
  const portalRoot = useMemo(() => {
    if (typeof document === "undefined") return null;
    const id = "modal-root";
    let el = document.getElementById(id);
    if (!el) {
      el = document.createElement("div");
      el.id = id;
      document.body.appendChild(el);
    }
    return el;
  }, []);

  // Inject styles once
  useEffect(() => {
    if (typeof document === "undefined") return;
    const STYLE_ID = "animica-modal-styles";
    if (!document.getElementById(STYLE_ID)) {
      const style = document.createElement("style");
      style.id = STYLE_ID;
      style.textContent = `
:root {
  --modal-backdrop: color-mix(in oklab, #000 65%, transparent);
  --modal-surface: var(--surface-elev-3, #0f0f10);
  --modal-border: var(--border, #2a2a2a);
  --modal-fg: var(--fg, #e9e9ea);
  --modal-muted: var(--fg-muted, #b9b9bd);
}
.modal__overlay {
  position: fixed; inset: 0;
  display: grid; place-items: center;
  background: var(--modal-backdrop);
  z-index: 1000;
  opacity: 0;
  animation: modal-fade-in 160ms ease-out forwards;
}
.modal__overlay[data-leave="true"] {
  animation: modal-fade-out 140ms ease-in forwards;
}
.modal__panel {
  width: 100%;
  max-height: 88vh;
  overflow: auto;
  color: var(--modal-fg);
  background: var(--modal-surface);
  border: 1px solid var(--modal-border);
  border-radius: 14px;
  box-shadow: 0 20px 48px rgba(0,0,0,.35);
  transform: translate3d(0, 8px, 0);
  opacity: 0;
  animation: modal-pop-in 180ms ease-out forwards;
}
.modal__panel[data-leave="true"] {
  animation: modal-pop-out 120ms ease-in forwards;
}
.modal__sizesm { max-width: 420px; }
.modal__sizemd { max-width: 720px; }
.modal__sizelg { max-width: 960px; }
.modal__sizexl { max-width: 1200px; }
.modal__sizefullscreen {
  width: 100vw; height: 100vh; max-width: none; max-height: none; border-radius: 0;
}
.modal__header {
  display: grid; grid-template-columns: 1fr auto;
  align-items: center; gap: 12px;
  padding: 14px 16px 8px 16px; position: sticky; top: 0;
  background: linear-gradient(to bottom, color-mix(in oklab, var(--modal-surface) 96%, transparent), transparent);
  backdrop-filter: blur(0px);
  z-index: 1;
}
.modal__title { font-weight: 700; letter-spacing: .01em; }
.modal__body { padding: 8px 16px 16px 16px; }
.modal__footer {
  display: flex; gap: 10px; justify-content: flex-end; padding: 8px 16px 16px 16px;
}
.modal__close {
  appearance: none; border: 0; background: transparent; color: var(--modal-muted);
  font-size: 18px; line-height: 1; border-radius: 8px; padding: 6px; cursor: pointer;
}
.modal__close:hover { color: var(--modal-fg); background: color-mix(in oklab, var(--modal-fg) 10%, transparent); }

@keyframes modal-fade-in { from { opacity: 0 } to { opacity: 1 } }
@keyframes modal-fade-out { from { opacity: 1 } to { opacity: 0 } }
@keyframes modal-pop-in {
  from { transform: translate3d(0, 8px, 0) scale(.98); opacity: 0; }
  to { transform: translate3d(0, 0, 0) scale(1); opacity: 1; }
}
@keyframes modal-pop-out {
  from { transform: translate3d(0, 0, 0) scale(1); opacity: 1; }
  to { transform: translate3d(0, 6px, 0) scale(.985); opacity: 0; }
}
@media (prefers-reduced-motion: reduce) {
  .modal__overlay, .modal__panel { animation: none !important; opacity: 1 !important; transform: none !important; }
}
      `;
      document.head.appendChild(style);
    }
  }, []);

  const overlayRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);
  const activeBeforeOpen = useRef<Element | null>(null);
  const leaving = useRef(false);

  // Body scroll lock when open
  useEffect(() => {
    if (typeof document === "undefined") return;
    if (isOpen) {
      activeBeforeOpen.current = document.activeElement;
      const prevOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
      return () => {
        document.body.style.overflow = prevOverflow || "";
        // restore focus
        const prev = activeBeforeOpen.current as HTMLElement | null;
        if (prev && prev.focus) {
          try { prev.focus(); } catch {}
        }
      };
    }
  }, [isOpen]);

  // Focus trap
  const focusFirst = useCallback(() => {
    if (!panelRef.current) return;
    if (initialFocusRef?.current) {
      initialFocusRef.current.focus();
      return;
    }
    const focusables = getFocusable(panelRef.current);
    if (focusables.length) focusables[0].focus();
    else panelRef.current.focus();
  }, [initialFocusRef]);

  useEffect(() => {
    if (!isOpen) return;
    const t = setTimeout(focusFirst, 0);
    return () => clearTimeout(t);
  }, [isOpen, focusFirst]);

  const onKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (!panelRef.current) return;
      if (e.key === "Escape" && closeOnEsc) {
        e.stopPropagation();
        e.preventDefault();
        safeClose(onClose, overlayRef, panelRef, leaving);
        return;
      }
      if (e.key === "Tab") {
        const focusables = getFocusable(panelRef.current);
        if (!focusables.length) {
          e.preventDefault();
          return;
        }
        const currentIndex = focusables.indexOf(document.activeElement as HTMLElement);
        let nextIndex = currentIndex;
        if (e.shiftKey) nextIndex = currentIndex <= 0 ? focusables.length - 1 : currentIndex - 1;
        else nextIndex = currentIndex === focusables.length - 1 ? 0 : currentIndex + 1;
        e.preventDefault();
        focusables[nextIndex]?.focus();
      }
    },
    [closeOnEsc, onClose]
  );

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => onKeyDown(e);
    window.addEventListener("keydown", handler, true);
    return () => window.removeEventListener("keydown", handler, true);
  }, [isOpen, onKeyDown]);

  const onBackdropClick = (e: React.MouseEvent) => {
    if (!closeOnBackdrop) return;
    if (e.target === overlayRef.current) {
      safeClose(onClose, overlayRef, panelRef, leaving);
    }
  };

  const labelledId = useMemo(() => {
    if (ariaLabelledBy) return ariaLabelledBy;
    if (!title) return undefined;
    return "modal-title-" + idSuffix(panelRef);
  }, [ariaLabelledBy, title]);

  if (!portalRoot || !isOpen) return null;

  return createPortal(
    <div
      className="modal__overlay"
      ref={overlayRef}
      onMouseDown={onBackdropClick}
      aria-hidden={false}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledId}
        aria-describedby={ariaDescribedBy}
        className={[
          "modal__panel",
          sizeClass(size),
        ].join(" ")}
        ref={panelRef}
        tabIndex={-1}
      >
        <div className="modal__header">
          <div className="modal__title" id={labelledId}>
            {title}
          </div>
          {!hideCloseButton && (
            <button
              ref={closeBtnRef}
              className="modal__close"
              aria-label="Close dialog"
              title="Close"
              onClick={() => safeClose(onClose, overlayRef, panelRef, leaving)}
            >
              ✕
            </button>
          )}
        </div>
        <div className="modal__body">{children}</div>
        {actions && <div className="modal__footer">{actions}</div>}
      </div>
    </div>,
    portalRoot
  );
};

export default Modal;

/* Utils */

function sizeClass(size: ModalSize) {
  switch (size) {
    case "sm":
      return "modal__sizesm";
    case "md":
      return "modal__sizemd";
    case "lg":
      return "modal__sizelg";
    case "xl":
      return "modal__sizexl";
    case "fullscreen":
      return "modal__sizefullscreen";
  }
}

function idSuffix(ref: React.RefObject<HTMLElement>) {
  return Math.random().toString(36).slice(2, 8) + (ref.current ? ref.current.tabIndex : "");
}

function getFocusable(root: HTMLElement): HTMLElement[] {
  const sel =
    'a[href], area[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), iframe, object, embed, [tabindex]:not([tabindex="-1"]), [contenteditable="true"]';
  const nodes = Array.from(root.querySelectorAll<HTMLElement>(sel));
  return nodes.filter((el) => isVisible(el));
}

function isVisible(el: HTMLElement) {
  const style = window.getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none";
}

function safeClose(
  onClose: () => void,
  overlayRef: React.RefObject<HTMLDivElement>,
  panelRef: React.RefObject<HTMLDivElement>,
  leavingFlag: React.MutableRefObject<boolean>
) {
  if (leavingFlag.current) return;
  leavingFlag.current = true;
  try {
    // play leave animations by toggling data-leave
    if (overlayRef.current) overlayRef.current.setAttribute("data-leave", "true");
    if (panelRef.current) panelRef.current.setAttribute("data-leave", "true");
    const prefersReduced =
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) {
      onClose();
    } else {
      setTimeout(onClose, 140); // sync with leave animations
    }
  } catch {
    onClose();
  } finally {
    setTimeout(() => {
      leavingFlag.current = false;
    }, 200);
  }
}
