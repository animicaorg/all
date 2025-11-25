/**
 * Utils: formatting hashes, integers, bytes, and simple pluralization.
 * These are small, dependency-free helpers intended for UI display.
 */

type Numeric = number | bigint;

/* --------------------------------- Hashes --------------------------------- */

/**
 * Ensures a hex string has a 0x prefix and normalized case.
 */
export function normalizeHex(
  hex: string,
  opts: { lowercase?: boolean; uppercase?: boolean } = {}
): string {
  const with0x = hex.startsWith('0x') || hex.startsWith('0X') ? hex : `0x${hex}`;
  if (opts.uppercase) return with0x.toUpperCase();
  if (opts.lowercase ?? true) return with0x.toLowerCase();
  return with0x;
}

/**
 * Produces a short hash like `0x1234…cdef`.
 *
 * @param hex - hex string with or without 0x
 * @param opts.prefix - number of visible hex chars after 0x (default 4)
 * @param opts.suffix - number of visible trailing hex chars (default 4)
 * @param opts.uppercase - output uppercase hex (default false)
 */
export function shortHash(
  hex: string,
  opts: { prefix?: number; suffix?: number; uppercase?: boolean } = {}
): string {
  const { prefix = 4, suffix = 4, uppercase = false } = opts;
  if (!hex) return '';
  const h = normalizeHex(hex, { lowercase: !uppercase, uppercase });
  // Keep "0x" separate from body for slicing
  const body = h.slice(2);
  if (body.length <= prefix + suffix) return h;
  const head = body.slice(0, prefix);
  const tail = body.slice(-suffix);
  return `0x${head}…${tail}`;
}

/* ------------------------------- Integers --------------------------------- */

/**
 * Formats integers with thousand separators. Works for number & bigint.
 * Uses Intl.NumberFormat where available, with a reasonable fallback.
 */
export function formatInt(n: Numeric, locale?: string): string {
  try {
    // @ts-ignore BigInt has toLocaleString in modern runtimes
    return (n as any).toLocaleString(locale ?? undefined);
  } catch {
    // Fallback: basic grouping for positive numbers
    const s = typeof n === 'bigint' ? n.toString() : Math.trunc(n).toString();
    const neg = s.startsWith('-');
    const digits = neg ? s.slice(1) : s;
    const grouped = digits.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return neg ? `-${grouped}` : grouped;
  }
}

/**
 * Compact integer formatting (e.g., 1.2K, 3.4M).
 * Only safe for JS numbers; for huge values pass a Number-safe approximation.
 */
export function formatIntCompact(n: number, locale?: string, maximumFractionDigits = 1): string {
  return new Intl.NumberFormat(locale ?? undefined, {
    notation: 'compact',
    maximumFractionDigits,
  }).format(n);
}

/* --------------------------------- Bytes ---------------------------------- */

const IEC_UNITS = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'] as const;
const SI_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'] as const;

export function formatBytes(
  bytes: Numeric,
  opts: {
    base?: 1000 | 1024;             // default 1024 (IEC)
    decimals?: number;              // default 2
    locale?: string;                // for decimal formatting
    pad?: boolean;                  // pad trailing zeros to decimals length (default false)
    unitSpacing?: 'narrow' | 'wide' // default 'narrow' (e.g., "1.2MiB" vs "1.2 MiB")
  } = {}
): string {
  const {
    base = 1024,
    decimals = 2,
    locale,
    pad = false,
    unitSpacing = 'narrow',
  } = opts;

  const units = base === 1000 ? SI_UNITS : IEC_UNITS;

  // Handle zero early
  if ((typeof bytes === 'bigint' && bytes === 0n) || (typeof bytes === 'number' && !isFinite(bytes)) || Number(bytes) === 0) {
    const sep = unitSpacing === 'wide' ? ' ' : '';
    return `0${sep}${units[0]}`;
  }

  if (typeof bytes === 'bigint') {
    const b = bytes < 0n ? -bytes : bytes;
    const sign = bytes < 0n ? '-' : '';
    const bigBase = BigInt(base);

    let idx = 0;
    let val = b;
    // Find unit by dividing while >= base
    while (val >= bigBase && idx < units.length - 1) {
      val /= bigBase;
      idx++;
    }

    // For BigInt, we compute a decimal with fixed places by scaling
    const scale = BigInt(10 ** Math.min(decimals, 6));
    let rem = b;
    let unitPow = 1n;
    for (let i = 0; i < idx; i++) unitPow *= bigBase;
    rem = b % unitPow;
    const whole = b / unitPow;

    const fracScaled = (rem * scale) / unitPow; // 0..(scale-1)
    const fraction = decimals > 0 ? `.${fracScaled.toString().padStart(scale.toString().length - 1, '0').slice(0, decimals)}` : '';
    const sep = unitSpacing === 'wide' ? ' ' : '';
    return `${sign}${whole.toString()}${fraction}${sep}${units[idx]}`;
  } else {
    const negative = bytes < 0;
    const b = Math.abs(bytes);
    const idx = Math.min(Math.floor(Math.log(b) / Math.log(base)), units.length - 1);
    const value = b / Math.pow(base, idx);

    const nf = new Intl.NumberFormat(locale ?? undefined, {
      minimumFractionDigits: pad ? decimals : 0,
      maximumFractionDigits: decimals,
    });

    const sep = unitSpacing === 'wide' ? ' ' : '';
    return `${negative ? '-' : ''}${nf.format(value)}${sep}${units[idx]}`;
  }
}

/* ------------------------------- Pluralize -------------------------------- */

/**
 * Returns a string with pluralized unit: `pluralize(1, "block") -> "1 block"`,
 * `pluralize(2, "block") -> "2 blocks"`, `pluralize(2, "child", "children") -> "2 children"`.
 */
export function pluralize(n: Numeric, singular: string, plural?: string): string {
  const num = typeof n === 'bigint' ? Number(n) : n;
  const word = num === 1 ? singular : (plural ?? `${singular}s`);
  return `${formatInt(n)} ${word}`;
}

/* --------------------------------- Extras --------------------------------- */

/**
 * Formats a byte array / hex-like into a short display string.
 * If given Uint8Array, it is hex-encoded first.
 */
export function shortBytes(
  v: string | Uint8Array,
  opts: { prefix?: number; suffix?: number; uppercase?: boolean } = {}
): string {
  if (typeof v === 'string') return shortHash(v, opts);
  const hex = `0x${[...v].map(b => b.toString(16).padStart(2, '0')).join('')}`;
  return shortHash(hex, opts);
}

/**
 * Simple signed number with a leading +/−, using locale separators.
 * Useful for deltas and metrics.
 */
export function formatSigned(n: number, locale?: string): string {
  const s = new Intl.NumberFormat(locale ?? undefined, { maximumFractionDigits: 2 }).format(Math.abs(n));
  return `${n < 0 ? '−' : '+'}${s}`;
}

/**
 * Clamp a numeric to a range and format compact (e.g., "99+").
 */
export function formatCappedCount(n: number, cap = 99): string {
  if (n <= cap) return `${n}`;
  return `${cap}+`;
}

/* ------------------------------- Exports ---------------------------------- */

export default {
  normalizeHex,
  shortHash,
  formatInt,
  formatIntCompact,
  formatBytes,
  pluralize,
  shortBytes,
  formatSigned,
  formatCappedCount,
};
