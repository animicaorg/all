import { BRAND } from "./constants";
import { safeNumber } from "./amounts";

const nf = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const nfCompact = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 2
});

/**
 * Adaptive number formatter — tiny values stay legible (token prices like
 * 0.0000298 used to collapse to "0" with maxFractionDigits=2). Big values
 * use 2 decimals; sub-1 values bump precision up to 8 significant digits.
 */
function smartFormat(n: number, opts: { compact?: boolean } = {}): string {
  if (!Number.isFinite(n)) return "0";
  const abs = Math.abs(n);
  if (opts.compact) {
    if (abs > 0 && abs < 0.01) {
      return n.toPrecision(3);
    }
    return nfCompact.format(n);
  }
  if (abs === 0) return "0";
  if (abs >= 1) return nf.format(n);
  // For sub-1 values, use significant-digit precision so 0.0000298 renders as
  // "0.0000298" instead of being rounded to 0. Cap at 8 digits to stay tidy.
  const digits = abs < 1e-6 ? 4 : 6;
  return n.toPrecision(digits).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export function formatAnm(amount: string | number | null | undefined, opts: { compact?: boolean } = {}) {
  const n = safeNumber(amount, 0);
  return `${smartFormat(n, opts)} ${BRAND.coin}`;
}

export function formatNumber(n: string | number | null | undefined, opts: { compact?: boolean } = {}) {
  return smartFormat(safeNumber(n, 0), opts);
}

export function shortAddress(address: string | null | undefined, head = 5, tail = 4) {
  if (!address) return "";
  if (address.length <= head + tail + 1) return address;
  return `${address.slice(0, head)}…${address.slice(-tail)}`;
}

export function timeAgo(input: string | number | Date) {
  const date = input instanceof Date ? input : new Date(input);
  const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
  if (diffSec < 5) return "just now";
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.floor(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const d = Math.floor(hr / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.floor(mo / 12)}y ago`;
}

export function pct(value: number, opts: { signed?: boolean } = {}) {
  const sign = opts.signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}
