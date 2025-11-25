import React, { KeyboardEvent, useMemo } from "react";
import cn from "../../utils/classnames";

export type LegendLayout = "row" | "column" | "wrap";
export type LegendSize = "sm" | "md" | "lg";

export interface LegendItem {
  id: string;
  label: string;
  color: string;       // any CSS color or gradient
  value?: number;      // optional numeric value (for totals/percent)
  active?: boolean;    // default true
  hint?: string;       // optional tooltip/title
  disabled?: boolean;  // non-interactive item
}

export interface LegendProps {
  items: LegendItem[];

  /** Toggle callback. If omitted, legend renders non-interactive. */
  onToggle?: (id: string, nextActive: boolean) => void;

  /** Optional bulk control visible when onToggle is provided. */
  showControls?: boolean;
  onSetAll?: (active: boolean) => void;

  /** Layout and sizing */
  layout?: LegendLayout; // default 'wrap'
  size?: LegendSize;     // default 'md'

  /** Value/percent rendering */
  showValues?: boolean;      // default: true if any value present
  showPercents?: boolean;    // default: false
  total?: number;            // override for percent denominator
  formatValue?: (n: number) => string;
  formatPercent?: (p: number) => string;

  className?: string;
  "data-testid"?: string;
}

const SIZE: Record<LegendSize, { swatch: number; gap: number; text: string; value: string }> = {
  sm: { swatch: 8,  gap: 6,  text: "legend-text-sm",  value: "legend-value-sm" },
  md: { swatch: 10, gap: 8,  text: "legend-text-md",  value: "legend-value-md" },
  lg: { swatch: 12, gap: 10, text: "legend-text-lg",  value: "legend-value-lg" },
};

const DEFAULTS = {
  layout: "wrap" as LegendLayout,
  size: "md" as LegendSize,
};

function defaultFormatValue(n: number): string {
  // Compact, locale-aware
  try {
    return new Intl.NumberFormat(undefined, {
      maximumFractionDigits: 2,
      notation: "compact",
      compactDisplay: "short",
    }).format(n);
  } catch {
    return String(Math.round(n * 100) / 100);
  }
}
function defaultFormatPercent(p: number): string {
  const pct = p * 100;
  const digits = pct < 1 ? 2 : pct < 10 ? 1 : 0;
  return `${pct.toFixed(digits)}%`;
}

export default function Legend({
  items,
  onToggle,
  showControls,
  onSetAll,
  layout = DEFAULTS.layout,
  size = DEFAULTS.size,
  showValues,
  showPercents = false,
  total,
  formatValue = defaultFormatValue,
  formatPercent = defaultFormatPercent,
  className,
  "data-testid": testId = "legend",
}: LegendProps) {
  const normalized = useMemo(
    () =>
      (items || []).map((it) => ({
        ...it,
        active: it.active !== undefined ? it.active : true,
      })),
    [items]
  );

  const anyValues = useMemo(
    () => normalized.some((i) => typeof i.value === "number"),
    [normalized]
  );
  const shouldShowValues = showValues ?? anyValues;

  const totalValue = useMemo(() => {
    if (typeof total === "number") return total;
    return normalized.reduce((acc, it) => acc + (typeof it.value === "number" ? it.value : 0), 0);
  }, [normalized, total]);

  const handleKey = (e: KeyboardEvent<HTMLButtonElement>, id: string, next: boolean) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onToggle?.(id, next);
    }
  };

  if (!normalized.length) return null;

  const s = SIZE[size];

  return (
    <div
      className={cn(
        "legend",
        layoutClass(layout),
        className
      )}
      data-testid={testId}
      role="list"
      aria-label="Chart legend"
    >
      {onToggle && showControls && (
        <div className="legend-controls" aria-label="Legend controls">
          <button
            type="button"
            className="legend-ctrl btn-all"
            onClick={() => onSetAll ? onSetAll(true) : normalized.forEach((i) => onToggle(i.id, true))}
          >
            All
          </button>
          <button
            type="button"
            className="legend-ctrl btn-none"
            onClick={() => onSetAll ? onSetAll(false) : normalized.forEach((i) => onToggle(i.id, false))}
          >
            None
          </button>
        </div>
      )}

      {normalized.map((it) => {
        const pct = showPercents && totalValue > 0 && typeof it.value === "number"
          ? it.value / totalValue
          : undefined;

        const interactive = !!onToggle && !it.disabled;
        const ariaPressed = !!it.active;

        return (
          <div
            key={it.id}
            className={cn("legend-item", !it.active && "legend-item--inactive", it.disabled && "legend-item--disabled")}
            role="listitem"
            title={it.hint || it.label}
          >
            {interactive ? (
              <button
                type="button"
                className="legend-btn"
                role="switch"
                aria-checked={ariaPressed}
                aria-label={`Toggle ${it.label}`}
                onClick={() => onToggle?.(it.id, !it.active)}
                onKeyDown={(e) => handleKey(e, it.id, !it.active)}
              >
                <Swatch size={s.swatch} color={it.color} active={!!it.active} />
                <span className={cn("legend-label", s.text)}>{it.label}</span>
                {shouldShowValues && typeof it.value === "number" && (
                  <span className={cn("legend-value", s.value)}>{formatValue(it.value)}</span>
                )}
                {typeof pct === "number" && (
                  <span className={cn("legend-percent", s.value)}>{formatPercent(pct)}</span>
                )}
              </button>
            ) : (
              <div className="legend-static">
                <Swatch size={s.swatch} color={it.color} active={!!it.active} />
                <span className={cn("legend-label", s.text)}>{it.label}</span>
                {shouldShowValues && typeof it.value === "number" && (
                  <span className={cn("legend-value", s.value)}>{formatValue(it.value)}</span>
                )}
                {typeof pct === "number" && (
                  <span className={cn("legend-percent", s.value)}>{formatPercent(pct)}</span>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ------------------------------- Swatch ----------------------------------- */

function Swatch({ size, color, active }: { size: number; color: string; active: boolean }) {
  const style: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: 2,
    background: color,
    border: "1px solid var(--legend-swatch-border, rgba(0,0,0,0.2))",
    opacity: active ? 1 : 0.4,
    flex: "0 0 auto",
  };
  return <span className="legend-swatch" style={style} aria-hidden="true" />;
}

/* ------------------------------ Utilities --------------------------------- */

function layoutClass(layout: LegendLayout): string {
  switch (layout) {
    case "row":
      return "legend--row";
    case "column":
      return "legend--column";
    case "wrap":
    default:
      return "legend--wrap";
  }
}

/* ------------------------------- CSS Notes ---------------------------------

This component relies on your global CSS. Suggested minimal styles:

.legend { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
.legend--row { flex-wrap: nowrap; }
.legend--column { flex-direction: column; align-items: flex-start; }
.legend-item { display: flex; }
.legend-item--inactive { opacity: 0.6; }
.legend-item--disabled { opacity: 0.4; pointer-events: none; }
.legend-btn, .legend-static {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 4px 6px; border-radius: 6px; background: transparent; border: none;
}
.legend-btn { cursor: pointer; }
.legend-btn:focus-visible { outline: 2px solid var(--focus, #94a3b8); outline-offset: 2px; }
.legend-label { white-space: nowrap; }
.legend-value, .legend-percent { color: var(--legend-muted, #6b7280); }

Size hooks (map to your type scale as desired):
.legend-text-sm { font-size: 11px; }
.legend-text-md { font-size: 12px; }
.legend-text-lg { font-size: 13px; }
.legend-value-sm { font-size: 11px; }
.legend-value-md { font-size: 12px; }
.legend-value-lg { font-size: 13px; }

.controls:
.legend-controls { display: inline-flex; gap: 8px; margin-right: 8px; }
.legend-ctrl { font-size: 12px; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--border, #e5e7eb); background: var(--surface, #fff); cursor: pointer; }
.legend-ctrl:hover { background: var(--surface-hover, #f8fafc); }

--------------------------------------------------------------------------- */

