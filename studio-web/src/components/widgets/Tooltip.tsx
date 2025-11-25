import React, {
  CSSProperties,
  ReactElement,
  ReactNode,
  cloneElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/**
 * Tooltip — lightweight, accessible, dependency-free.
 *
 * Features
 * - Hover OR keyboard-focus triggers
 * - Optional "interactive" mode keeps tooltip open while hovered
 * - Smart placement with simple viewport collision flipping
 * - Optional portal to document.body
 * - Escape key to dismiss
 * - ARIA-compliant (`role="tooltip"`, `aria-describedby`)
 */

type Placement = "top" | "bottom" | "left" | "right";
type Align = "start" | "center" | "end";

export interface TooltipProps {
  /** The element that triggers the tooltip (must be a single ReactElement) */
  children: ReactElement;
  /** Tooltip content (text or any node) */
  content: ReactNode;
  /** Preferred side; may flip on collision */
  placement?: Placement;
  /** Alignment along the cross axis */
  align?: Align;
  /** Pixel gap between trigger and tooltip */
  offset?: number;
  /** Show delay (ms) */
  delayShow?: number;
  /** Hide delay (ms) */
  delayHide?: number;
  /** Disable tooltip entirely */
  disabled?: boolean;
  /** Keep visible when pointer enters the tooltip panel */
  interactive?: boolean;
  /** Render in a portal (document.body) */
  portal?: boolean;
  /** Max width (px) */
  maxWidth?: number;
  /** Class for the tooltip panel */
  className?: string;
  /** Inline style for the tooltip panel */
  style?: CSSProperties;
}

type Coords = {
  top: number;
  left: number;
  placement: Placement;
  transformOrigin: string;
  arrowX: number;
  arrowY: number;
};

const ARROW_SIZE = 8; // px (diagonal square used as arrow)

export const Tooltip: React.FC<TooltipProps> = ({
  children,
  content,
  placement = "top",
  align = "center",
  offset = 8,
  delayShow = 80,
  delayHide = 60,
  disabled = false,
  interactive = false,
  portal = true,
  maxWidth = 280,
  className,
  style,
}) => {
  const id = useId().replace(/:/g, "-");
  const triggerRef = useRef<HTMLElement | null>(null);
  const tipRef = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<Coords | null>(null);
  const showTimer = useRef<number | null>(null);
  const hideTimer = useRef<number | null>(null);
  const insideTooltip = useRef(false);

  const clearTimers = () => {
    if (showTimer.current) {
      window.clearTimeout(showTimer.current);
      showTimer.current = null;
    }
    if (hideTimer.current) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
  };

  const scheduleShow = () => {
    if (disabled) return;
    clearTimers();
    showTimer.current = window.setTimeout(() => setOpen(true), delayShow);
  };
  const scheduleHide = () => {
    if (disabled) return;
    clearTimers();
    hideTimer.current = window.setTimeout(() => {
      if (!interactive || !insideTooltip.current) setOpen(false);
    }, delayHide);
  };

  const computePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const tip = tipRef.current;
    if (!trigger || !tip) return;

    const tr = trigger.getBoundingClientRect();
    const tw = tip.offsetWidth;
    const th = tip.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;

    // Helper to compute base coords for a given placement/alignment
    const baseFor = (pl: Placement): { top: number; left: number; transformOrigin: string; arrowX: number; arrowY: number } => {
      let top = 0, left = 0, transformOrigin = "50% 50%";
      let arrowX = 0, arrowY = 0;

      if (pl === "top" || pl === "bottom") {
        // horizontal alignment
        let cx = tr.left + tr.width / 2;
        if (align === "start") cx = tr.left;
        if (align === "end") cx = tr.right;

        left = Math.round(cx - tw / 2);
        if (pl === "top") {
          top = Math.round(tr.top - th - offset - ARROW_SIZE / Math.SQRT2);
          transformOrigin = "50% 100%";
          arrowX = Math.min(Math.max(cx - left - ARROW_SIZE / 2, ARROW_SIZE), tw - ARROW_SIZE);
          arrowY = th - 1;
        } else {
          top = Math.round(tr.bottom + offset + ARROW_SIZE / Math.SQRT2);
          transformOrigin = "50% 0%";
          arrowX = Math.min(Math.max(cx - left - ARROW_SIZE / 2, ARROW_SIZE), tw - ARROW_SIZE);
          arrowY = -1;
        }
      } else {
        // vertical alignment
        let cy = tr.top + tr.height / 2;
        if (align === "start") cy = tr.top;
        if (align === "end") cy = tr.bottom;

        top = Math.round(cy - th / 2);
        if (pl === "left") {
          left = Math.round(tr.left - tw - offset - ARROW_SIZE / Math.SQRT2);
          transformOrigin = "100% 50%";
          arrowX = tw - 1;
          arrowY = Math.min(Math.max(cy - top - ARROW_SIZE / 2, ARROW_SIZE), th - ARROW_SIZE);
        } else {
          left = Math.round(tr.right + offset + ARROW_SIZE / Math.SQRT2);
          transformOrigin = "0% 50%";
          arrowX = -1;
          arrowY = Math.min(Math.max(cy - top - ARROW_SIZE / 2, ARROW_SIZE), th - ARROW_SIZE);
        }
      }
      // Account for scroll
      top += window.scrollY;
      left += window.scrollX;
      return { top, left, transformOrigin, arrowX, arrowY };
    };

    // Try preferred placement then flip on collision
    const tryOrder: Placement[] = [placement];
    const opposite: Record<Placement, Placement> = { top: "bottom", bottom: "top", left: "right", right: "left" };
    tryOrder.push(opposite[placement]);

    let chosen: Placement = placement;
    let pos = baseFor(chosen);

    // Collision detection in viewport
    const fits = (p: { top: number; left: number }) => {
      const pad = 4;
      const t = p.top - window.scrollY;
      const l = p.left - window.scrollX;
      return t >= pad && l >= pad && t + th <= vh - pad && l + tw <= vw - pad;
    };

    if (!fits(pos) && tryOrder.length > 1) {
      const alt = baseFor(tryOrder[1]);
      if (fits(alt)) {
        chosen = tryOrder[1];
        pos = alt;
      } else {
        // clamp within viewport as a last resort
        const pad = 4;
        pos.top = Math.min(Math.max(pos.top, window.scrollY + pad), window.scrollY + vh - th - pad);
        pos.left = Math.min(Math.max(pos.left, window.scrollX + pad), window.scrollX + vw - tw - pad);
      }
    }

    setCoords({
      top: pos.top,
      left: pos.left,
      placement: chosen,
      transformOrigin: pos.transformOrigin,
      arrowX: pos.arrowX,
      arrowY: pos.arrowY,
    });
  }, [placement, align, offset]);

  useLayoutEffect(() => {
    if (!open) return;
    computePosition();
  }, [open, computePosition, content]);

  // Reposition on resize/scroll while open
  useEffect(() => {
    if (!open) return;
    const handler = () => computePosition();
    window.addEventListener("scroll", handler, { passive: true });
    window.addEventListener("resize", handler);
    const ro = new ResizeObserver(handler);
    if (triggerRef.current) ro.observe(triggerRef.current);
    if (tipRef.current) ro.observe(tipRef.current);
    return () => {
      window.removeEventListener("scroll", handler);
      window.removeEventListener("resize", handler);
      ro.disconnect();
    };
  }, [open, computePosition]);

  // Escape closes
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Touch support: tap-and-hold shows briefly
  useEffect(() => {
    const el = triggerRef.current;
    if (!el) return;
    let longPressTimer: number | null = null;
    const onTouchStart = () => {
      if (disabled) return;
      longPressTimer = window.setTimeout(() => setOpen(true), 180);
    };
    const onTouchEnd = () => {
      if (longPressTimer) window.clearTimeout(longPressTimer);
      longPressTimer = null;
      window.setTimeout(() => setOpen(false), 1200);
    };
    el.addEventListener("touchstart", onTouchStart, { passive: true });
    el.addEventListener("touchend", onTouchEnd);
    el.addEventListener("touchcancel", onTouchEnd);
    return () => {
      el.removeEventListener("touchstart", onTouchStart);
      el.removeEventListener("touchend", onTouchEnd);
      el.removeEventListener("touchcancel", onTouchEnd);
    };
  }, [disabled]);

  const child = React.Children.only(children);
  const childProps = {
    ref: (node: HTMLElement) => {
      // merge refs
      triggerRef.current = node;
      const childRef: any = (child as any).ref;
      if (typeof childRef === "function") childRef(node);
      else if (childRef && typeof childRef === "object") (childRef as any).current = node;
    },
    onMouseEnter: (e: React.MouseEvent) => {
      (child.props as any).onMouseEnter?.(e);
      scheduleShow();
    },
    onMouseLeave: (e: React.MouseEvent) => {
      (child.props as any).onMouseLeave?.(e);
      scheduleHide();
    },
    onFocus: (e: React.FocusEvent) => {
      (child.props as any).onFocus?.(e);
      scheduleShow();
    },
    onBlur: (e: React.FocusEvent) => {
      (child.props as any).onBlur?.(e);
      scheduleHide();
    },
    "aria-describedby": open ? id : undefined,
  };

  const panel = open && coords ? (
    <div
      ref={tipRef}
      id={id}
      role="tooltip"
      style={{
        position: "absolute",
        top: coords.top,
        left: coords.left,
        zIndex: 1000,
        maxWidth,
        pointerEvents: interactive ? "auto" : "none",
        // panel styles
        background: "var(--tooltip-bg, rgba(17,17,17,0.94))",
        color: "var(--tooltip-fg, #fff)",
        padding: "8px 10px",
        borderRadius: 8,
        fontSize: 12,
        lineHeight: 1.25,
        boxShadow:
          "0 8px 20px rgba(0,0,0,0.25), 0 2px 6px rgba(0,0,0,0.18)",
        transformOrigin: coords.transformOrigin,
        ...(style || {}),
      }}
      className={className}
      onMouseEnter={() => {
        insideTooltip.current = true;
        if (interactive) clearTimers();
      }}
      onMouseLeave={() => {
        insideTooltip.current = false;
        if (interactive) scheduleHide();
      }}
    >
      {/* Arrow (rotated square) */}
      <span
        aria-hidden="true"
        style={{
          position: "absolute",
          width: ARROW_SIZE,
          height: ARROW_SIZE,
          transform: "rotate(45deg)",
          background: "var(--tooltip-bg, rgba(17,17,17,0.94))",
          top:
            coords.placement === "bottom"
              ? -ARROW_SIZE / 2
              : coords.placement === "top"
              ? undefined
              : coords.arrowY,
          bottom:
            coords.placement === "top" ? -ARROW_SIZE / 2 : undefined,
          left:
            coords.placement === "right"
              ? -ARROW_SIZE / 2
              : coords.placement === "left"
              ? undefined
              : coords.arrowX,
          right:
            coords.placement === "left" ? -ARROW_SIZE / 2 : undefined,
          boxShadow:
            "0 2px 6px rgba(0,0,0,0.18)",
        }}
      />
      {content}
    </div>
  ) : null;

  useEffect(() => {
    return () => clearTimers();
  }, []);

  return (
    <>
      {cloneElement(child, childProps)}
      {portal ? (panel ? createPortal(panel, document.body) : null) : panel}
    </>
  );
};

export default Tooltip;
