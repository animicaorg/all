import type { MarketCandle, MarketData, Quote, QuoteRequest, TransactionRequest } from "@launchpad/shared";
import { safeNumber, TRADE_FEE_BPS_DEFAULT } from "@launchpad/shared";
import type { TradeContext, TradingAdapter } from "./types";
import { TradingError } from "./types";

const FEE = TRADE_FEE_BPS_DEFAULT / 10_000;

/**
 * Animica bonding-curve adapter.
 *
 * Per platform constraint: every project launches with the same linear curve:
 *   p(x) = P0 + K * x, where x = tokens already sold
 *   P0 = $0.001 (starting price, USD)
 *   At x = 30,000,000 (target sale supply) the cumulative spend = $5,000,000
 *
 * Solve K from the integral:
 *   ∫₀ᴺ (P0 + K·x) dx = P0·N + K·N²/2 = TARGET_RAISE
 *   K = 2 (TARGET_RAISE − P0·N) / N²
 */
export const BONDING_P0_USD = 0.001;
export const BONDING_TARGET_SUPPLY = 30_000_000;
export const BONDING_TARGET_RAISE_USD = 5_000_000;
export const BONDING_K_USD =
  (2 * (BONDING_TARGET_RAISE_USD - BONDING_P0_USD * BONDING_TARGET_SUPPLY)) /
  (BONDING_TARGET_SUPPLY * BONDING_TARGET_SUPPLY);

export interface BondingConfig {
  /** USD reference price for 1 ANM. Defaults to 1 (so ANM≡USD numerically). */
  anmUsdPrice?: number;
  /** Tokens already sold on the curve. */
  soldSupply?: number;
}

function priceUsdAt(x: number): number {
  return BONDING_P0_USD + BONDING_K_USD * x;
}

function cumulativeRaiseUsd(x: number): number {
  return BONDING_P0_USD * x + (BONDING_K_USD * x * x) / 2;
}

/** Tokens output for spending `dUsd` starting at supply `x0`. */
function tokensOutForUsd(x0: number, dUsd: number): number {
  // Solve P0·dx + K·(x0·dx + dx²/2) = dUsd
  // => (K/2)·dx² + (P0 + K·x0)·dx − dUsd = 0
  const a = BONDING_K_USD / 2;
  const b = BONDING_P0_USD + BONDING_K_USD * x0;
  const c = -dUsd;
  const disc = b * b - 4 * a * c;
  if (disc < 0) return 0;
  return (-b + Math.sqrt(disc)) / (2 * a);
}

/** USD received for selling `dToken` starting at supply `x0`. */
function usdOutForTokens(x0: number, dToken: number): number {
  const x1 = Math.max(0, x0 - dToken);
  return cumulativeRaiseUsd(x0) - cumulativeRaiseUsd(x1);
}

export class AnimicaBondingCurveAdapter implements TradingAdapter {
  readonly id = "animica-bonding";
  readonly label = "Animica Bonding Curve";
  readonly active = false;

  constructor(private cfg: BondingConfig = {}) {}

  private get anmUsd(): number {
    return this.cfg.anmUsdPrice ?? 1;
  }

  /** Convert USD → ANM amount. */
  private usdToAnm(usd: number) {
    return usd / this.anmUsd;
  }
  /** Convert ANM amount → USD. */
  private anmToUsd(anm: number) {
    return anm * this.anmUsd;
  }

  async getQuote(_ctx: TradeContext, req: QuoteRequest): Promise<Quote> {
    const x0 = this.cfg.soldSupply ?? 0;
    const slippageBps = req.slippageBps ?? 100;
    if (req.side === "BUY") {
      const anmIn = safeNumber(req.amountInAnm, 0);
      const feeAnm = anmIn * FEE;
      const netAnm = anmIn - feeAnm;
      const usdIn = this.anmToUsd(netAnm);
      const tokensOut = tokensOutForUsd(x0, usdIn);
      const priceNowUsd = priceUsdAt(x0);
      return {
        side: "BUY",
        inputAmount: anmIn.toString(),
        outputAmount: tokensOut.toFixed(6),
        priceAnm: this.usdToAnm(priceNowUsd).toFixed(10),
        feeAnm: feeAnm.toFixed(6),
        minOutput: (tokensOut * (1 - slippageBps / 10_000)).toFixed(6),
        routeLabel: "bonding-curve",
        expiresAt: new Date(Date.now() + 30_000).toISOString()
      };
    }
    const tokensIn = safeNumber(req.amountInToken, 0);
    const usdOut = usdOutForTokens(x0, tokensIn);
    const grossAnm = this.usdToAnm(usdOut);
    const feeAnm = grossAnm * FEE;
    const anmOut = grossAnm - feeAnm;
    const priceNowUsd = priceUsdAt(x0);
    return {
      side: "SELL",
      inputAmount: tokensIn.toString(),
      outputAmount: anmOut.toFixed(6),
      priceAnm: this.usdToAnm(priceNowUsd).toFixed(10),
      feeAnm: feeAnm.toFixed(6),
      minOutput: (anmOut * (1 - slippageBps / 10_000)).toFixed(6),
      routeLabel: "bonding-curve",
      expiresAt: new Date(Date.now() + 30_000).toISOString()
    };
  }

  async createBuyTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new TradingError(
      "NOT_ACTIVE",
      "Bonding-curve trading is not yet wired to a live Animica route."
    );
  }
  async createSellTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new TradingError(
      "NOT_ACTIVE",
      "Bonding-curve trading is not yet wired to a live Animica route."
    );
  }

  async getProjectMarketData(ctx: TradeContext): Promise<MarketData> {
    // Synthesize historical candles by walking the curve in supply steps.
    const x0 = this.cfg.soldSupply ?? 0;
    const candles: MarketCandle[] = [];
    const now = Date.now();
    const steps = 96;
    for (let i = steps - 1; i >= 0; i -= 1) {
      const supplyAt = Math.max(0, x0 - i * (x0 / steps || 1));
      const supplyPrev = Math.max(0, x0 - (i + 1) * (x0 / steps || 1));
      const oUsd = priceUsdAt(supplyPrev);
      const cUsd = priceUsdAt(supplyAt);
      candles.push({
        t: now - i * 5 * 60_000,
        o: this.usdToAnm(oUsd),
        h: this.usdToAnm(Math.max(oUsd, cUsd)),
        l: this.usdToAnm(Math.min(oUsd, cUsd)),
        c: this.usdToAnm(cUsd),
        v: 0
      });
    }
    const last = candles[candles.length - 1];
    const firstC = candles[0].c || last.c;
    const cap = this.usdToAnm(priceUsdAt(x0) * BONDING_TARGET_SUPPLY);
    return {
      candles,
      priceAnm: last.c.toFixed(10),
      priceChange24h: ((last.c - firstC) / Math.max(firstC, 1e-12)) * 100,
      liquidityAnm: this.usdToAnm(cumulativeRaiseUsd(x0)).toFixed(2),
      volume24hAnm: "0",
      marketCapAnm: cap.toFixed(2)
    };
  }
}

export const bondingMath = {
  P0_USD: BONDING_P0_USD,
  K_USD: BONDING_K_USD,
  TARGET_SUPPLY: BONDING_TARGET_SUPPLY,
  TARGET_RAISE_USD: BONDING_TARGET_RAISE_USD,
  priceUsdAt,
  cumulativeRaiseUsd,
  tokensOutForUsd,
  usdOutForTokens
};
