import { BRAND } from "./constants";

export const ANM_DECIMALS = BRAND.decimals;

export function toBaseUnits(human: string | number, decimals = ANM_DECIMALS): bigint {
  const str = typeof human === "number" ? human.toString() : human.trim();
  if (!/^\d+(\.\d+)?$/.test(str)) {
    throw new Error(`Invalid numeric amount: ${str}`);
  }
  const [whole, frac = ""] = str.split(".");
  const padded = (frac + "0".repeat(decimals)).slice(0, decimals);
  return BigInt(whole) * 10n ** BigInt(decimals) + BigInt(padded || "0");
}

export function fromBaseUnits(base: string | bigint, decimals = ANM_DECIMALS): string {
  const b = typeof base === "bigint" ? base : BigInt(base);
  const negative = b < 0n;
  const abs = negative ? -b : b;
  const s = abs.toString().padStart(decimals + 1, "0");
  const whole = s.slice(0, s.length - decimals);
  const frac = s.slice(s.length - decimals).replace(/0+$/, "");
  const result = frac ? `${whole}.${frac}` : whole;
  return negative ? `-${result}` : result;
}

export function safeNumber(x: string | number | undefined | null, fallback = 0): number {
  if (x == null) return fallback;
  const n = typeof x === "number" ? x : Number(x);
  return Number.isFinite(n) ? n : fallback;
}
