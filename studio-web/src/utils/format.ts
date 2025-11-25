/**
 * format.ts — human formatting helpers for gas, sizes, and addresses.
 *
 * Zero deps. Safe for both browser and SSR. All functions are pure.
 */

export type Numeric = number | bigint | string;

/** Guard: is a finite number-like (number or bigint or numeric string) */
function toBigIntStrict(n: Numeric): bigint {
  if (typeof n === 'bigint') return n;
  if (typeof n === 'number') {
    if (!Number.isFinite(n)) throw new TypeError('Value must be finite');
    if (!Number.isInteger(n)) return BigInt(Math.trunc(n));
    return BigInt(n);
  }
  if (typeof n === 'string') {
    const s = n.trim();
    if (!s) throw new TypeError('Empty numeric string');
    // support hex 0x.. and decimal
    if (/^0x[0-9a-fA-F]+$/.test(s)) return BigInt(s);
    if (/^[+-]?\d+$/.test(s)) return BigInt(s);
    throw new TypeError(`Invalid numeric string: ${n}`);
  }
  // @ts-expect-error exhaustive
  throw new TypeError('Unsupported numeric type');
}

/** Format an integer with thousands separators. Works up to bigint range. */
export function formatInteger(n: Numeric): string {
  const bi = toBigIntStrict(n);
  const sign = bi < 0n ? '-' : '';
  let s = (bi < 0n ? -bi : bi).toString(10);

  // Insert commas every 3 digits from the right
  let out = '';
  while (s.length > 3) {
    const seg = s.slice(-3);
    out = `,${seg}${out}`;
    s = s.slice(0, -3);
  }
  out = s + out;
  return sign + out;
}

/** Generic middle truncation for identifiers (addresses, hashes, etc.) */
export function truncateMiddle(value: string, prefix = 6, suffix = 4, ellipsis = '…'): string {
  const v = value ?? '';
  if (v.length <= prefix + suffix + 1) return v;
  return `${v.slice(0, prefix)}${ellipsis}${v.slice(-suffix)}`;
}

/** Normalize hex string to 0x-prefixed lowercase (no validation beyond hex chars). */
export function normalizeHex(hex: string): string {
  if (!hex) return '0x';
  const h = hex.startsWith('0x') || hex.startsWith('0X') ? hex.slice(2) : hex;
  if (h.length === 0) return '0x';
  if (!/^[0-9a-fA-F]+$/.test(h)) return '0x';
  return '0x' + h.toLowerCase();
}

/** Heuristic: looks like bech32/bech32m (e.g., anim1...) */
function looksBech32(addr: string): boolean {
  const i = addr.indexOf('1');
  if (i <= 0) return false;
  // HRP must be lowercase alpha/digits and rest be bech32 charset roughly
  const hrp = addr.slice(0, i);
  const data = addr.slice(i + 1);
  return /^[a-z0-9]+$/.test(hrp) && /^[qpzry9x8gf2tvdw0s3jn54khce6mua7l]+$/.test(data);
}

/**
 * Format address for UI:
 *  - If bech32-like → lowercase and truncated with hrp preserved if short.
 *  - If hex → normalized and truncated.
 *  - Else → safe middle truncation.
 */
export interface AddressFormatOptions {
  prefix?: number;
  suffix?: number;
  keepHrp?: boolean; // if true and bech32, keep "hrp1" prefix intact
  ellipsis?: string;
}

export function formatAddress(addr: string, opts: AddressFormatOptions = {}): string {
  const { prefix = 6, suffix = 4, keepHrp = true, ellipsis = '…' } = opts;
  if (!addr) return '';
  const trimmed = addr.trim();

  if (looksBech32(trimmed.toLowerCase())) {
    const lower = trimmed.toLowerCase();
    if (!keepHrp) return truncateMiddle(lower, prefix, suffix, ellipsis);
    const idx = lower.indexOf('1');
    if (idx > -1 && lower.length > idx + 1 + suffix + 1) {
      const head = lower.slice(0, idx + 1); // includes '1'
      return `${head}${lower.slice(idx + 1, idx + 1 + prefix)}${ellipsis}${lower.slice(-suffix)}`;
    }
    return lower;
  }

  if (/^(0x)?[0-9a-fA-F]{8,}$/.test(trimmed)) {
    const norm = normalizeHex(trimmed);
    return truncateMiddle(norm, prefix + 2 /* account for 0x */, suffix, ellipsis);
  }

  return truncateMiddle(trimmed, prefix, suffix, ellipsis);
}

/** Format 32-byte hash (hex) with normalization + truncation. */
export function formatHash(hash: string, prefix = 10, suffix = 8): string {
  if (!hash) return '';
  const norm = normalizeHex(hash);
  return truncateMiddle(norm, prefix, suffix);
}

/** Byte size formatting */
export interface BytesFormatOptions {
  base?: 1000 | 1024;          // default 1024 (KiB)
  decimals?: number;            // fractional digits for non-bytes units
  narrow?: boolean;             // use narrow no-break space
  unit?: 'auto' | 'B' | 'KB' | 'MB' | 'GB' | 'TB' | 'KiB' | 'MiB' | 'GiB' | 'TiB';
}

const UNITS_1024 = ['B', 'KiB', 'MiB', 'GiB', 'TiB'] as const;
const UNITS_1000 = ['B', 'KB', 'MB', 'GB', 'TB'] as const;

export function formatBytes(value: Numeric, opts: BytesFormatOptions = {}): string {
  const base = opts.base ?? 1024;
  const decimals = Math.max(0, Math.min(6, opts.decimals ?? 2));
  const narrow = opts.narrow ?? true;
  const NBSP = narrow ? '\u202F' : ' ';
  const units = base === 1000 ? UNITS_1000 : UNITS_1024;

  let num = Number(toBigIntStrict(value)); // safe for display; if huge, JS number may lose precision in decimals only
  const forcedUnit = opts.unit ?? 'auto';

  // If a specific unit is requested, scale to that
  if (forcedUnit !== 'auto') {
    const idx = units.indexOf(forcedUnit as any);
    if (idx === -1) throw new Error(`Unsupported unit: ${forcedUnit}`);
    const scaled = num / Math.pow(base, idx);
    return `${scaled.toFixed(idx === 0 ? 0 : decimals)}${NBSP}${units[idx]}`;
  }

  let i = 0;
  while (i < units.length - 1 && Math.abs(num) >= base) {
    num /= base;
    i++;
  }

  const fixed = i === 0 ? 0 : decimals;
  return `${num.toFixed(fixed)}${NBSP}${units[i]}`;
}

/** Gas formatting */
export interface GasFormatOptions {
  style?: 'plain' | 'units';   // 'plain' → "123,456"; 'units' → "123.4 kGas"
  decimals?: number;           // for 'units' style
}

export function formatGas(value: Numeric, opts: GasFormatOptions = {}): string {
  const style = opts.style ?? 'units';
  if (style === 'plain') return formatInteger(value);

  const decimals = Math.max(0, Math.min(6, opts.decimals ?? 1));
  let n = Number(toBigIntStrict(value));

  const NBSP = '\u202F';
  if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(decimals)}${NBSP}Ggas`;
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(decimals)}${NBSP}Mgas`;
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(decimals)}${NBSP}kgas`;
  return `${formatInteger(n)}${NBSP}gas`;
}

/** Percentage formatter (0..1 → "12.3%") */
export function formatPercent01(value: number, decimals = 1): string {
  const pct = (value || 0) * 100;
  return `${pct.toFixed(decimals)}%`;
}

/** Short relative time (seconds) → "2m 3s" */
export function formatDurationSeconds(totalSeconds: number): string {
  if (!Number.isFinite(totalSeconds)) return '';
  const s = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const parts = [];
  if (h) parts.push(`${h}h`);
  if (m || h) parts.push(`${m}m`);
  parts.push(`${sec}s`);
  return parts.join(' ');
}

/** Convenience: format a balance-like bigint with decimals (e.g., 1e18) */
export function formatUnits(amount: Numeric, decimals = 18, maxFrac = 6): string {
  const bi = toBigIntStrict(amount);
  const neg = bi < 0n;
  const abs = neg ? -bi : bi;
  const base = 10n ** BigInt(decimals);
  const whole = abs / base;
  const frac = abs % base;

  if (maxFrac <= 0) return (neg ? '-' : '') + formatInteger(whole);

  const fracStr = frac.toString().padStart(decimals, '0').slice(0, maxFrac).replace(/0+$/, '');
  return (neg ? '-' : '') + (fracStr ? `${formatInteger(whole)}.${fracStr}` : formatInteger(whole));
}

/** Format a fee (price * gas) when both are integers; returns human string with chosen unit label. */
export function formatFee(totalFee: Numeric, symbol = 'ANM', decimals = 18, maxFrac = 6): string {
  return `${formatUnits(totalFee, decimals, maxFrac)} ${symbol}`;
}

export default {
  formatInteger,
  truncateMiddle,
  normalizeHex,
  formatAddress,
  formatHash,
  formatBytes,
  formatGas,
  formatPercent01,
  formatDurationSeconds,
  formatUnits,
  formatFee,
};
