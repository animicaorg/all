import React from "react";
import { createPortal } from "react-dom";

type Side = "top" | "bottom" | "left" | "right";
type Align = "center" | "start" | "end";

export interface TooltipProps {
  /** The anchor element to wrap (e.g., a button). */
  children: React.ReactElement;
  /** Tooltip body. Can be React nodes or a function returning nodes. */
  content: React.ReactNode | (() => React.ReactNode);
  /** Preferred side to place the tooltip. Will auto-flip on collisions. */
  side?: Side;
  /** Alignment along the secondary axis. */
  align?: Align;
  /** Gap in pixels between anchor and tooltip. */
  offset?: number;
  /** Max width in pixels (helps wrapping long text). */
  maxWidth?: number;
  /** Open state (controlled). */
  open?: boolean;
  /** Initial open state (uncontrolled). */
  defaultOpen?: boolean;
  /** Called when open state changes (for controlled usage). */
  onOpenChange?: (open: boolean) => void;
  /** Delay before opening on hover/focus (ms). */
  delayOpen?: number;
  /** Delay before closing after leave/blur (ms). */
  delayClose?: number;
  /** Disable the tooltip entirely. */
  disabled?: boolean;
  /** Allow pointer to move into tooltip without closing. */
  interactive?: boolean;
  /** Render into document.body via portal (recommended). */
  portal?: boolean;
  /** Extra class for the floating content. */
  contentClassName?: string;
  /** Show the arrow indicator. */
  showArrow?: boolean;
  /** z-index for the floating layer. */
  zIndex?: number;
  /** Callback after the floating element has been positioned. */
  onPosition?: (data: { x: number; y: number; side: Side; align: Align }) => void;
}

type PositionOpts = {
  side: Side;
  align: Align;
  offset: number;
  margin: number;
};

const ARROW = { size: 8 }; // px; rendered as a rotated square
const DEFAULTS = {
  side: "top" as Side,
  align: "center" as Align,
  offset: 8,
  delayOpen: 150,
  delayClose: 100,
  margin: 8,
  maxWidth: 320,
  zIndex: 50,
};

function isTouchLike(e: PointerEvent | React.PointerEvent) {
  return e.pointerType === "touch" || e.pointerType === "pen";
}

function getViewportRect() {
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    x: 0,
    y: 0,
  };
}

function computePosition(
  anchor: HTMLElement,
  floating: HTMLElement,
  opts: PositionOpts
): { x: number; y: number; side: Side; align: Align } {
  const { side, align, offset, margin } = opts;
  const a = anchor.getBoundingClientRect();
  const f = floating.getBoundingClientRect();
  const vp = getViewportRect();

  const primary = (s: Side) => {
    switch (s) {
      case "top":
        return { x: a.left + a.width / 2 - f.width / 2, y: a.top - f.height - offset };
      case "bottom":
        return { x: a.left + a.width / 2 - f.width / 2, y: a.bottom + offset };
      case "left":
        return { x: a.left - f.width - offset, y: a.top + a.height / 2 - f.height / 2 };
      case "right":
      default:
        return { x: a.right + offset, y: a.top + a.height / 2 - f.height / 2 };
    }
  };

  const applyAlign = (base: { x: number; y: number }, s: Side) => {
    const out = { ...base };
    if (s === "top" || s === "bottom") {
      if (align === "start") out.x = a.left;
      if (align === "end") out.x = a.right - f.width;
    } else {
      if (align === "start") out.y = a.top;
      if (align === "end") out.y = a.bottom - f.height;
    }
    return out;
  };

  const clamp = (v: number, min: number, max: number) => Math.max(min, Math.min(max, v));

  let s: Side = side;
  let base = applyAlign(primary(s), s);

  // Collision detection + flip if needed (simple heuristic)
  const overflowsTop = base.y < margin;
  const overflowsBottom = base.y + f.height > vp.height - margin;
  const overflowsLeft = base.x < margin;
  const overflowsRight = base.x + f.width > vp.width - margin;

  if ((s === "top" && overflowsTop) || (s === "bottom" && overflowsBottom)) {
    s = s === "top" ? "bottom" : "top";
    base = applyAlign(primary(s), s);
  } else if ((s === "left" && overflowsLeft) || (s === "right" && overflowsRight)) {
    s = s === "left" ? "right" : "left";
    base = applyAlign(primary(s), s);
  }

  // Shift within viewport on the secondary axis
  if (s === "top" || s === "bottom") {
    base.x = clamp(base.x, margin, vp.width - margin - f.width);
  } else {
    base.y = clamp(base.y, margin, vp.height - margin - f.height);
  }

  // Account for scroll
  return {
    x: Math.round(base.x + window.scrollX),
    y: Math.round(base.y + window.scrollY),
    side: s,
    align,
  };
}

export function Tooltip({
  children,
  content,
  side = DEFAULTS.side,
  align = DEFAULTS.align,
  offset = DEFAULTS.offset,
  maxWidth = DEFAULTS.maxWidth,
  open,
  defaultOpen,
  onOpenChange,
  delayOpen = DEFAULTS.delayOpen,
  delayClose = DEFAULTS.delayClose,
  disabled,
  interactive = false,
  portal = true,
  contentClassName,
  showArrow = true,
  zIndex = DEFAULTS.zIndex,
  onPosition,
}: TooltipProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = React.useState(!!defaultOpen);
  const isControlled = typeof open === "boolean";
  const isOpen = isControlled ? !!open : uncontrolledOpen;
  const setOpen = (v: boolean) => (isControlled ? onOpenChange?.(v) : setUncontrolledOpen(v));

  const id = React.useId();
  const anchorRef = React.useRef<HTMLElement | null>(null);
  const floatRef = React.useRef<HTMLDivElement | null>(null);
  const timers = React.useRef<{ open?: number; close?: number }>({});

  const stateRef = React.useRef({ pointerInsideFloat: false, pointerInsideAnchor: false });

  // Attach refs to child
  const child = React.Children.only(children);
  const mergedRef = (node: HTMLElement | null) => {
    anchorRef.current = node;
    const childRef = (child as any).ref;
    if (typeof childRef === "function") childRef(node);
    else if (childRef && typeof childRef === "object") (childRef as React.MutableRefObject<any>).current = node;
  };

  const clearTimers = () => {
    if (timers.current.open) window.clearTimeout(timers.current.open);
    if (timers.current.close) window.clearTimeout(timers.current.close);
    timers.current.open = undefined;
    timers.current.close = undefined;
  };

  const requestOpen = (delay = delayOpen) => {
    if (disabled) return;
    clearTimers();
    timers.current.open = window.setTimeout(() => setOpen(true), Math.max(0, delay));
  };

  const requestClose = (delay = delayClose) => {
    clearTimers();
    timers.current.close = window.setTimeout(() => {
      if (!interactive || !stateRef.current.pointerInsideFloat) setOpen(false);
    }, Math.max(0, delay));
  };

  const immediateClose = () => {
    clearTimers();
    setOpen(false);
  };

  // Positioning effect
  const position = React.useCallback(() => {
    if (!anchorRef.current || !floatRef.current) return;
    const data = computePosition(anchorRef.current, floatRef.current, {
      side,
      align,
      offset: offset + (showArrow ? ARROW.size / 2 : 0),
      margin: DEFAULTS.margin,
    });
    const el = floatRef.current;
    el.style.left = `${data.x}px`;
    el.style.top = `${data.y}px`;
    el.dataset.side = data.side;
    el.dataset.align = data.align;
    onPosition?.(data);
  }, [side, align, offset, showArrow, onPosition]);

  // Reposition on open, resize, scroll, and element size changes
  React.useEffect(() => {
    if (!isOpen) return;
    position();

    const obs = new ResizeObserver(() => position());
    if (anchorRef.current) obs.observe(anchorRef.current);
    if (floatRef.current) obs.observe(floatRef.current);

    const onScroll = () => position();
    const onResize = () => position();
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);

    return () => {
      obs.disconnect();
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onResize);
    };
  }, [isOpen, position]);

  // Close on ESC
  React.useEffect(() => {
    if (!isOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") immediateClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen]);

  // Close on outside click
  React.useEffect(() => {
    if (!isOpen) return;
    const onDown = (e: MouseEvent) => {
      const a = anchorRef.current;
      const f = floatRef.current;
      const t = e.target as Node;
      if (a && a.contains(t)) return;
      if (f && f.contains(t)) return;
      immediateClose();
    };
    document.addEventListener("pointerdown", onDown, { capture: true });
    return () => document.removeEventListener("pointerdown", onDown, { capture: true } as any);
  }, [isOpen]);

  // Anchor event handlers
  const onPointerEnterAnchor = (e: React.PointerEvent) => {
    stateRef.current.pointerInsideAnchor = true;
    if (disabled) return;
    // For touch/pen, open on tap and toggle on repeated taps
    if (isTouchLike(e)) {
      setOpen(!isOpen);
    } else {
      requestOpen();
    }
  };

  const onPointerLeaveAnchor = () => {
    stateRef.current.pointerInsideAnchor = false;
    if (!interactive) requestClose();
    else {
      // If interactive, wait for pointer to possibly enter the floating element
      requestClose(150);
    }
  };

  const onFocusAnchor = () => requestOpen(0);
  const onBlurAnchor = () => requestClose(0);

  // Floating handlers (interactive mode)
  const onPointerEnterFloat = () => {
    stateRef.current.pointerInsideFloat = true;
    clearTimers();
  };
  const onPointerLeaveFloat = () => {
    stateRef.current.pointerInsideFloat = false;
    if (!stateRef.current.pointerInsideAnchor) requestClose();
  };

  const anchorProps: React.HTMLAttributes<any> = {
    ref: mergedRef as any,
    onPointerEnter: onPointerEnterAnchor,
    onPointerLeave: onPointerLeaveAnchor,
    onFocus: onFocusAnchor,
    onBlur: onBlurAnchor,
    "aria-describedby": isOpen ? id : undefined,
  };

  const layer = (
    <div
      ref={floatRef}
      id={id}
      role="tooltip"
      className={`tooltip-layer ${isOpen ? "open" : "closed"}`}
      data-state={isOpen ? "open" : "closed"}
      style={{ position: "absolute", zIndex }}
      onPointerEnter={interactive ? onPointerEnterFloat : undefined}
      onPointerLeave={interactive ? onPointerLeaveFloat : undefined}
    >
      <div
        className={`tooltip-content ${contentClassName || ""}`}
        style={{ maxWidth }}
      >
        {typeof content === "function" ? content() : content}
        {showArrow && <span className="tooltip-arrow" aria-hidden="true" />}
      </div>
      <style>{css}</style>
    </div>
  );

  return (
    <>
      {React.cloneElement(child, anchorProps)}
      {typeof document !== "undefined" && isOpen
        ? portal
          ? createPortal(layer, document.body)
          : layer
        : null}
    </>
  );
}

const css = `
.tooltip-layer {
  pointer-events: none; /* enable through-content hover behavior */
  opacity: 0;
  transform: translateY(0);
  transition: opacity .12s ease, transform .12s ease;
}
.tooltip-layer[data-state="open"] {
  opacity: 1;
}
.tooltip-content {
  pointer-events: auto; /* allow interactive content when enabled */
  background: rgba(17, 24, 39, 0.96); /* near-black */
  color: white;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  line-height: 1.25;
  box-shadow: 0 6px 24px rgba(0,0,0,.18), 0 2px 8px rgba(0,0,0,.12);
  position: relative;
  will-change: transform;
}

/* Subtle enter slide depending on side */
.tooltip-layer[data-side="top"][data-state="open"] .tooltip-content { transform: translateY(-2px); }
.tooltip-layer[data-side="bottom"][data-state="open"] .tooltip-content { transform: translateY(2px); }
.tooltip-layer[data-side="left"][data-state="open"] .tooltip-content { transform: translateX(-2px); }
.tooltip-layer[data-side="right"][data-state="open"] .tooltip-content { transform: translateX(2px); }

.tooltip-arrow {
  position: absolute;
  width: ${ARROW.size * 2}px;
  height: ${ARROW.size * 2}px;
  transform: rotate(45deg);
  background: inherit;
  box-shadow: inherit;
  /* Shadow mask: draw only one-side shadow to match bubble */
  filter: drop-shadow(0 2px 4px rgba(0,0,0,.15));
}

/* Arrow placement per side */
.tooltip-layer[data-side="top"] .tooltip-arrow {
  bottom: -${ARROW.size}px;
  left: calc(50% - ${ARROW.size}px);
}
.tooltip-layer[data-side="bottom"] .tooltip-arrow {
  top: -${ARROW.size}px;
  left: calc(50% - ${ARROW.size}px);
}
.tooltip-layer[data-side="left"] .tooltip-arrow {
  right: -${ARROW.size}px;
  top: calc(50% - ${ARROW.size}px);
}
.tooltip-layer[data-side="right"] .tooltip-arrow {
  left: -${ARROW.size}px;
  top: calc(50% - ${ARROW.size}px);
}

/* Align start/end nudges arrow a bit */
.tooltip-layer[data-side="top"][data-align="start"] .tooltip-arrow,
.tooltip-layer[data-side="bottom"][data-align="start"] .tooltip-arrow {
  left: 16px;
}
.tooltip-layer[data-side="top"][data-align="end"] .tooltip-arrow,
.tooltip-layer[data-side="bottom"][data-align="end"] .tooltip-arrow {
  right: 16px; left: auto;
}
.tooltip-layer[data-side="left"][data-align="start"] .tooltip-arrow,
.tooltip-layer[data-side="right"][data-align="start"] .tooltip-arrow {
  top: 12px;
}
.tooltip-layer[data-side="left"][data-align="end"] .tooltip-arrow,
.tooltip-layer[data-side="right"][data-align="end"] .tooltip-arrow {
  bottom: 12px; top: auto;
}

/* High-contrast mode helpers */
@media (prefers-contrast: more) {
  .tooltip-content {
    outline: 2px solid rgba(255,255,255,.7);
    outline-offset: 0;
  }
}
`;

// Convenience hook for imperative tooltips (advanced)
export function useTooltipControls() {
  const [open, setOpen] = React.useState(false);
  const api = React.useMemo(
    () => ({
      open: () => setOpen(true),
      close: () => setOpen(false),
      toggle: () => setOpen((v) => !v),
      get state() {
        return open;
      },
    }),
    [open]
  );
  return api;
}

export default Tooltip;
