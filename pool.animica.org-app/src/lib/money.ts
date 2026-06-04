// Decimal-based money helpers. Everything user-facing lives as strings in
// the DB so we never round-trip through JS number; this module is the
// shared lens.

import Decimal from "decimal.js";

Decimal.set({ precision: 38, rounding: Decimal.ROUND_HALF_EVEN });

export const D = (v: Decimal.Value): Decimal => new Decimal(v ?? 0);

export function add(a: Decimal.Value, b: Decimal.Value): string {
  return D(a).plus(D(b)).toString();
}

export function sub(a: Decimal.Value, b: Decimal.Value): string {
  return D(a).minus(D(b)).toString();
}

export function mul(a: Decimal.Value, b: Decimal.Value): string {
  return D(a).times(D(b)).toString();
}

export function div(a: Decimal.Value, b: Decimal.Value): string {
  return D(a).div(D(b)).toString();
}

export function pctOf(amount: Decimal.Value, pct: Decimal.Value): string {
  return D(amount).times(D(pct)).div(100).toString();
}

export function gte(a: Decimal.Value, b: Decimal.Value): boolean {
  return D(a).gte(D(b));
}
export function lte(a: Decimal.Value, b: Decimal.Value): boolean {
  return D(a).lte(D(b));
}
export function gt(a: Decimal.Value, b: Decimal.Value): boolean {
  return D(a).gt(D(b));
}
export function lt(a: Decimal.Value, b: Decimal.Value): boolean {
  return D(a).lt(D(b));
}
export function eq(a: Decimal.Value, b: Decimal.Value): boolean {
  return D(a).eq(D(b));
}

/** Format a decimal string to N significant digits, dropping trailing zeros. */
export function fmt(value: Decimal.Value, dp = 6): string {
  const d = D(value);
  if (d.isNaN()) return "0";
  return d.toDecimalPlaces(dp, Decimal.ROUND_HALF_EVEN).toString();
}

/** Same as fmt, but always returns at least N decimal places — useful
 *  for displaying prices stably (`0.001000` instead of `0.001`). */
export function fmtFixed(value: Decimal.Value, dp = 6): string {
  const d = D(value);
  if (d.isNaN()) return "0";
  return d.toFixed(dp, Decimal.ROUND_HALF_EVEN);
}
