/**
 * Chart theme & number formatting helpers shared by all chart components.
 * - Zero runtime deps (safe for SSR)
 * - Locale-aware compact numbers
 * - Light/Dark palettes
 * - Small utilities for alpha blending & contrast
 */

export type ThemeMode = "light" | "dark";

export interface ChartTheme {
  mode: ThemeMode;

  // Colors
  background: string;
  surface: string;
  grid: string;
  axis: string;
  textPrimary: string;
  textMuted: string;
  tooltipBg: string;
  tooltipBorder: string;

  // Data series palette
  series: string[];

  // Typographic scale for canvas/SVG renderers
  fontFamily: string;
  fontSize: number; // px
  lineWidth: number;

  // Helpers
  withAlpha: (color: string, alpha: number) => string;
  contrastOn: (bg: string) => string;
}

const PALETTE_LIGHT = [
  "#2563eb", // blue-600
  "#16a34a", // green-600
  "#f59e0b", // amber-500
  "#ef4444", // red-500
  "#8b5cf6", // violet-500
  "#0891b2", // cyan-600
  "#84cc16", // lime-500
  "#d946ef", // fuchsia-500
  "#f97316", // orange-500
  "#10b981", // emerald-500
];

const PALETTE_DARK = [
  "#60a5fa", // blue-400
  "#34d399", // emerald-400
  "#fbbf24", // amber-400
  "#f87171", // red-400
  "#a78bfa", // violet-400
  "#22d3ee", // cyan-400
  "#a3e635", // lime-400
  "#e879f9", // fuchsia-400
  "#fb923c", // orange-400
  "#6ee7b7", // emerald-300
];

const BASE_LIGHT = {
  background: "#ffffff",
  surface: "#ffffff",
  grid: "#e5e7eb", // gray-200
  axis: "#9ca3af", // gray-400
  textPrimary: "#111827", // gray-900
  textMuted: "#6b7280", // gray-500
  tooltipBg: "rgba(255,255,255,0.96)",
  tooltipBorder: "rgba(17,24,39,0.12)",
};

const BASE_DARK = {
  background: "#0b1220", // deep navy-ish
  surface: "#0f172a", // slate-900
  grid: "rgba(255,255,255,0.12)",
  axis: "rgba(255,255,255,0.6)",
  textPrimary: "rgba(255,255,255,0.92)",
  textMuted: "rgba(255,255,255,0.65)",
  tooltipBg: "rgba(15,23,42,0.96)",
  tooltipBorder: "rgba(255,255,255,0.12)",
};

/** Safe, SSR-friendly media check */
export function prefersDark(): boolean {
  try {
    return typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches;
  } catch {
    return false;
  }
}

/** Simple hex -> rgba + fallback passthrough for non-hex inputs. */
export function withAlpha(color: string, alpha: number): string {
  const m = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(color.trim());
  if (!m) return color; // already rgba/var/keyword
  const hex = m[1];
  const to255 = (h: string) => parseInt(h, 16);
  let r: number, g: number, b: number;
  if (hex.length === 3) {
    r = to255(hex[0] + hex[0]);
    g = to255(hex[1] + hex[1]);
    b = to255(hex[2] + hex[2]);
  } else {
    r = to255(hex.slice(0, 2));
    g = to255(hex.slice(2, 4));
    b = to255(hex.slice(4, 6));
  }
  return `rgba(${r}, ${g}, ${b}, ${Math.max(0, Math.min(1, alpha))})`;
}

/** Contrast color (black/white) using relative luminance on hex colors; fallback to white. */
export function contrastOn(bg: string): string {
  const m = /^#([0-9a-f]{6})$/i.exec(bg.trim());
  if (!m) return "#ffffff";
  const hex = m[1];
  const to255 = (h: string) => parseInt(h, 16) / 255;
  const r = to255(hex.slice(0, 2));
  const g = to255(hex.slice(2, 4));
  const b = to255(hex.slice(4, 6));
  const lum = 0.2126 * gamma(r) + 0.7152 * gamma(g) + 0.0722 * gamma(b);
  return lum > 0.5 ? "#111827" : "#ffffff";
}
function gamma(c: number): number {
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** Construct a theme instance, optionally overriding palette or base tokens. */
export function makeChartTheme(
  mode?: ThemeMode,
  overrides?: Partial<ChartTheme>
): ChartTheme {
  const m: ThemeMode = mode ?? (prefersDark() ? "dark" : "light");
  const base = m === "dark" ? BASE_DARK : BASE_LIGHT;
  const series = m === "dark" ? PALETTE_DARK.slice() : PALETTE_LIGHT.slice();
  const theme: ChartTheme = {
    mode: m,
    ...base,
    series,
    fontFamily:
      '-apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, "Helvetica Neue", Arial, "Noto Sans", sans-serif',
    fontSize: 12,
    lineWidth: 2,
    withAlpha,
    contrastOn,
    ...(overrides || {}),
  };
  return theme;
}

/** Get a series color by index with wrap-around. */
export function seriesColor(idx: number, theme: ChartTheme): string {
  if (!theme.series.length) return theme.textPrimary;
  return theme.series[idx % theme.series.length];
}

/* ------------------------- Number formatting ------------------------------ */

export interface NumberFormats {
  number: (n: number) => string;           // generic compact numbers
  integer: (n: number) => string;          // integer formatting (no decimals)
  fixed: (n: number, frac?: number) => string; // fixed decimals with grouping
  percent: (p: number) => string;          // p in [0,1] → "12.3%"
  bytes: (b: number) => string;            // 1.2 MB
  amount: (n: number, sym?: string) => string; // chain amounts, e.g. "1.23 ANM"
  timeMs: (ms: number) => string;          // "123 ms", "1.2 s", "3.4 min"
}

export interface NumberFormatOptions {
  locale?: string;
  amountDecimals?: number; // default 6 (e.g., micro units)
  amountTrimZeros?: boolean; // default true
  maxFractionDigits?: number; // default 2 for compact number()
}

/**
 * Create locale-aware number formatters.
 * Keep defaults conservative and suitable for dashboard UIs.
 */
export function makeNumberFormats(opts: NumberFormatOptions = {}): NumberFormats {
  const {
    locale,
    amountDecimals = 6,
    amountTrimZeros = true,
    maxFractionDigits = 2,
  } = opts;

  const nfCompact = safeNF(locale, {
    notation: "compact",
    compactDisplay: "short",
    maximumFractionDigits: maxFractionDigits,
  });

  const nfInt = safeNF(locale, { maximumFractionDigits: 0 });
  const nfFixed = (digits: number) =>
    safeNF(locale, {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });

  const nfPercent = safeNF(locale, {
    style: "percent",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });

  function number(n: number): string {
    if (!isFinite(n)) return "–";
    return nfCompact.format(n);
    }

  function integer(n: number): string {
    if (!isFinite(n)) return "–";
    return nfInt.format(Math.round(n));
  }

  function fixed(n: number, frac = 2): string {
    if (!isFinite(n)) return "–";
    return nfFixed(frac).format(n);
  }

  function percent(p: number): string {
    if (!isFinite(p)) return "–";
    // Accept 0..1 or 0..100; normalize if > 1 but <= 100
    const v = p > 1 && p <= 100 ? p / 100 : p;
    return nfPercent.format(v);
  }

  function bytes(b: number): string {
    if (!isFinite(b)) return "–";
    const abs = Math.abs(b);
    if (abs < 1024) return `${Math.round(b)} B`;
    const units = ["KB", "MB", "GB", "TB", "PB"];
    let u = -1;
    let v = abs;
    do {
      v /= 1024;
      u++;
    } while (v >= 1024 && u < units.length - 1);
    const sign = b < 0 ? "-" : "";
    const str = v >= 100 ? Math.round(v).toString() : v.toFixed(v >= 10 ? 1 : 2);
    return `${sign}${str} ${units[u]}`;
  }

  function amount(n: number, sym = "ANM"): string {
    if (!isFinite(n)) return "–";
    // Show up to amountDecimals but trim trailing zeros by default
    const dec = Math.max(0, Math.min(8, amountDecimals));
    const raw = safeNF(locale, {
      minimumFractionDigits: amountTrimZeros ? 0 : dec,
      maximumFractionDigits: dec,
    }).format(n);
    return `${raw} ${sym}`;
  }

  function timeMs(ms: number): string {
    if (!isFinite(ms)) return "–";
    const abs = Math.abs(ms);
    const sign = ms < 0 ? "-" : "";
    if (abs < 1000) return `${sign}${Math.round(abs)} ms`;
    const s = abs / 1000;
    if (s < 60) return `${sign}${s.toFixed(s >= 10 ? 1 : 2)} s`;
    const m = s / 60;
    if (m < 60) return `${sign}${m.toFixed(m >= 10 ? 1 : 2)} min`;
    const h = m / 60;
    return `${sign}${h.toFixed(h >= 10 ? 1 : 2)} h`;
  }

  return { number, integer, fixed, percent, bytes, amount, timeMs };
}

/** Safe Intl.NumberFormat with fallback. */
function safeNF(locale: string | undefined, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  try {
    return new Intl.NumberFormat(locale, options);
  } catch {
    return new Intl.NumberFormat(undefined, options);
  }
}

/* ----------------------- Axis & tick helpers ------------------------------ */

export interface TickFormatter {
  (value: number, index?: number): string;
}

/** Build a tick formatter by kind. */
export function makeTickFormatter(
  kind: "number" | "percent" | "bytes" | "time" | "amount",
  fmts?: NumberFormats,
  amountSymbol?: string
): TickFormatter {
  const F = fmts ?? makeNumberFormats();
  switch (kind) {
    case "percent":
      return (v) => F.percent(v);
    case "bytes":
      return (v) => F.bytes(v);
    case "time":
      return (v) => F.timeMs(v);
    case "amount":
      return (v) => F.amount(v, amountSymbol);
    case "number":
    default:
      return (v) => F.number(v);
  }
}

/** Choose an aesthetically pleasing grid step for a numeric range. */
export function niceStep(min: number, max: number, targetTicks = 6): number {
  if (!isFinite(min) || !isFinite(max) || min === max) return 1;
  const span = Math.abs(max - min);
  const raw = span / Math.max(1, targetTicks);
  const pow10 = Math.pow(10, Math.floor(Math.log10(raw)));
  const candidates = [1, 2, 2.5, 5, 10].map((m) => m * pow10);
  let best = candidates[0];
  let bestDiff = Infinity;
  for (const c of candidates) {
    const diff = Math.abs(raw - c);
    if (diff < bestDiff) {
      best = c;
      bestDiff = diff;
    }
  }
  return best;
}

/** Build a translucent grid color from the theme axis color. */
export function gridColor(theme: ChartTheme, opacity = 0.3): string {
  return theme.withAlpha(theme.axis, opacity);
}

/** Export convenient singletons for most apps */
export const THEME_LIGHT: ChartTheme = makeChartTheme("light");
export const THEME_DARK: ChartTheme = makeChartTheme("dark");
export const FORMATS_DEFAULT: NumberFormats = makeNumberFormats();

/** Resolve theme by explicit mode or system preference. */
export function resolveTheme(mode?: ThemeMode): ChartTheme {
  return makeChartTheme(mode);
}
