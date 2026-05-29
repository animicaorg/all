/**
 * Pure-ANM linear bonding curve used by the Animica Launch native adapter.
 *
 * Curve definition (all in ANM, no USD pegging):
 *   p(x) = p0 + k * x          where x = tokens already sold
 *   cumulative ANM raised by x: R(x) = p0 * x + (k / 2) * x²
 *
 * Defaults are tuned so that the curve graduates after selling 30,000,000
 * tokens for an estimated 200,000 ANM total raise. The numbers are knobs;
 * override per env if the launchpad chain economics change.
 */
export interface BondingParams {
  /** Tokens (whole units, NOT base units). */
  saleSupply: number;
  /** Starting price in ANM per token. */
  startPriceAnm: number;
  /** Total ANM raised by the time saleSupply is exhausted. */
  targetRaiseAnm: number;
}

export const DEFAULT_PARAMS: BondingParams = {
  saleSupply: 30_000_000,
  startPriceAnm: 0.000_001,
  targetRaiseAnm: 200_000
};

export function paramsFromEnv(env: Record<string, string | undefined>): BondingParams {
  const num = (k: string, fallback: number) => {
    const v = env[k];
    if (!v) return fallback;
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : fallback;
  };
  return {
    saleSupply: num("LAUNCHPAD_CURVE_SALE_SUPPLY", DEFAULT_PARAMS.saleSupply),
    startPriceAnm: num("LAUNCHPAD_CURVE_START_PRICE_ANM", DEFAULT_PARAMS.startPriceAnm),
    targetRaiseAnm: num("LAUNCHPAD_CURVE_TARGET_RAISE_ANM", DEFAULT_PARAMS.targetRaiseAnm)
  };
}

export function curveK(p: BondingParams): number {
  return (2 * (p.targetRaiseAnm - p.startPriceAnm * p.saleSupply)) / (p.saleSupply * p.saleSupply);
}

export function priceAnmAt(x: number, p: BondingParams = DEFAULT_PARAMS): number {
  return p.startPriceAnm + curveK(p) * x;
}

export function cumulativeRaiseAnm(x: number, p: BondingParams = DEFAULT_PARAMS): number {
  return p.startPriceAnm * x + (curveK(p) * x * x) / 2;
}

/** Tokens received when spending `dAnm` starting at supply `x0`. */
export function tokensOutForAnm(x0: number, dAnm: number, p: BondingParams = DEFAULT_PARAMS): number {
  // Solve (k/2) dx² + (p0 + k x0) dx − dAnm = 0
  const k = curveK(p);
  const a = k / 2;
  const b = p.startPriceAnm + k * x0;
  const c = -dAnm;
  if (a === 0) return dAnm / Math.max(b, 1e-18);
  const disc = b * b - 4 * a * c;
  if (disc < 0) return 0;
  return (-b + Math.sqrt(disc)) / (2 * a);
}

/** ANM received when selling `dToken` starting at supply `x0`. */
export function anmOutForTokens(x0: number, dToken: number, p: BondingParams = DEFAULT_PARAMS): number {
  const x1 = Math.max(0, x0 - dToken);
  return cumulativeRaiseAnm(x0, p) - cumulativeRaiseAnm(x1, p);
}
