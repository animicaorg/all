import React, { useMemo } from "react";
import { cn } from "../../utils/classnames";
import {
  resolveTheme,
  seriesColor,
  FORMATS_DEFAULT,
  makeNumberFormats,
} from "../charts/chart.theme";

export type MixDatum =
  | { key: string; value: number; label?: string; color?: string }
  | { key: string; value: number; label?: string; color?: undefined };

export interface ProofMixLegendProps {
  /**
   * Either an array of { key, value } or a Record<key, value>.
   * Values should already represent the chosen totals (e.g. capped Σψ).
   */
  data: Array<MixDatum> | Record<string, number>;
  /** Controls list ordering; default: 'desc' by value. */
  order?: "desc" | "asc" | "alpha";
  /** Show percentage column (of total). Default: true. */
  showPercent?: boolean;
  /** Optional explicit theme mode; otherwise system. */
  themeMode?: "light" | "dark";
  /** Optional custom label mapping for keys. */
  labels?: Record<string, string>;
  /** Optional precomputed color mapping for stable cross-charts coloring. */
  colors?: Record<string, string>;
  className?: string;
}

/**
 * ProofMixLegend
 * Displays a compact legend with color swatches, labels, raw values, and percentage of total.
 * Useful alongside PoIES donut/bar charts to summarize capped Σψ mix.
 */
export const ProofMixLegend: React.FC<ProofMixLegendProps> = ({
  data,
  order = "desc",
  showPercent = true,
  themeMode,
  labels,
  colors,
  className,
}) => {
  const theme = resolveTheme(themeMode);
  const F = makeNumberFormats();

  const rows = useMemo(() => {
    const arr: Array<{ key: string; value: number; label: string }> = Array.isArray(data)
      ? data.map((d) => ({
          key: d.key,
          value: Number.isFinite((d as any).value) ? (d as any).value : 0,
          label: (d as any).label ?? labelize((d as any).key),
        }))
      : Object.entries(data).map(([k, v]) => ({
          key: k,
          value: Number.isFinite(v) ? v : 0,
          label: labels?.[k] ?? labelize(k),
        }));

    if (order === "alpha") {
      arr.sort((a, b) => a.label.localeCompare(b.label));
    } else if (order === "asc") {
      arr.sort((a, b) => a.value - b.value);
    } else {
      arr.sort((a, b) => b.value - a.value);
    }
    return arr;
  }, [data, order, labels]);

  const total = useMemo(
    () => rows.reduce((acc, r) => acc + (isFinite(r.value) ? r.value : 0), 0),
    [rows]
  );

  const withColors = useMemo(() => {
    return rows.map((r, i) => {
      const color =
        colors?.[r.key] ??
        seriesColor(i, theme); // Stable per-render deterministic palette
      const pct = total > 0 ? r.value / total : 0;
      return { ...r, color, pct };
    });
  }, [rows, colors, theme, total]);

  if (withColors.length === 0) {
    return (
      <div
        className={cn("rounded-md border p-3 text-sm", className)}
        style={{ background: theme.surface, borderColor: theme.grid, color: theme.textMuted }}
      >
        No data.
      </div>
    );
  }

  return (
    <div
      className={cn("rounded-md border p-3", className)}
      style={{ background: theme.surface, borderColor: theme.grid }}
      role="table"
      aria-label="Proof mix legend"
    >
      <div
        className="grid items-center gap-x-3 gap-y-1"
        style={{
          gridTemplateColumns: showPercent
            ? "auto 1fr auto auto"
            : "auto 1fr auto",
          color: theme.textPrimary,
        }}
        role="rowgroup"
      >
        {/* Header */}
        <div className="text-xs font-medium py-1" style={{ color: theme.textMuted }} role="columnheader" />
        <div className="text-xs font-medium py-1" style={{ color: theme.textMuted }} role="columnheader">
          Proof type
        </div>
        <div className="text-xs font-medium py-1 text-right whitespace-nowrap" style={{ color: theme.textMuted }} role="columnheader">
          Σψ
        </div>
        {showPercent && (
          <div className="text-xs font-medium py-1 text-right whitespace-nowrap" style={{ color: theme.textMuted }} role="columnheader">
            %
          </div>
        )}

        {/* Rows */}
        {withColors.map((r) => (
          <React.Fragment key={r.key}>
            <div className="py-1" role="cell">
              <span
                aria-hidden
                className="inline-block rounded-sm align-middle"
                style={{
                  width: 10,
                  height: 10,
                  background: r.color,
                }}
                title={r.label}
              />
            </div>
            <div className="py-1" role="cell">
              {r.label}
            </div>
            <div className="py-1 text-right tabular-nums" role="cell" title={`${r.value}`}>
              {F.number(r.value)}
            </div>
            {showPercent && (
              <div className="py-1 text-right tabular-nums" role="cell" title={`${(r.pct * 100).toFixed(4)}%`}>
                {FORMATS_DEFAULT.percent(r.pct)}
              </div>
            )}
          </React.Fragment>
        ))}

        {/* Footer total */}
        <div role="cell" className="pt-1 border-t" style={{ borderColor: theme.grid }} />
        <div role="cell" className="pt-1 border-t text-xs" style={{ borderColor: theme.grid, color: theme.textMuted }}>
          Total
        </div>
        <div role="cell" className="pt-1 border-t text-right tabular-nums" style={{ borderColor: theme.grid, color: theme.textPrimary }}>
          {F.number(total)}
        </div>
        {showPercent && (
          <div role="cell" className="pt-1 border-t text-right tabular-nums" style={{ borderColor: theme.grid, color: theme.textPrimary }}>
            {FORMATS_DEFAULT.percent(1)}
          </div>
        )}
      </div>
    </div>
  );
};

/**
 * Build a deterministic color map for a given list of keys,
 * using the shared seriesColor palette. Helpful to keep colors
 * stable between charts and legends.
 */
export function colorMapFromKeys(
  keys: string[],
  themeMode?: "light" | "dark"
): Record<string, string> {
  const theme = resolveTheme(themeMode);
  const map: Record<string, string> = {};
  keys.forEach((k, i) => {
    map[k] = seriesColor(i, theme);
  });
  return map;
}

/* --------------------------------- utils ---------------------------------- */

function labelize(key: string): string {
  const k = key.toLowerCase();
  if (k === "zk" || k === "zero_knowledge") return "ZK";
  if (k === "ai") return "AI";
  if (k === "quantum" || k === "q") return "Quantum";
  if (k === "da" || k === "data_availability") return "DA";
  if (k === "classical") return "Classical";
  return key.replace(/[_\-]+/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

export default ProofMixLegend;
