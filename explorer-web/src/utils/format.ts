export function formatNumber(n: number | bigint | string, maxFrac = 6): string {
  const val = typeof n === "string" ? Number(n) : Number(n);
  if (!Number.isFinite(val)) return String(n);
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: maxFrac }).format(val);
}
export { shortHash } from "./hash";
