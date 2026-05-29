import type { MarketCandle, MarketData, Quote, QuoteRequest, TransactionRequest } from "@launchpad/shared";
import { safeNumber, TRADE_FEE_BPS_DEFAULT } from "@launchpad/shared";
import type { TradeContext, TradingAdapter } from "./types";

const FEE = TRADE_FEE_BPS_DEFAULT / 10_000;

function seededRandom(seed: string) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h ^= h << 13;
    h ^= h >>> 17;
    h ^= h << 5;
    return ((h >>> 0) % 100_000) / 100_000;
  };
}

function buildCandles(seed: string, count = 96): MarketCandle[] {
  const rand = seededRandom(seed);
  const now = Date.now();
  let price = 0.0001 + rand() * 0.01;
  const candles: MarketCandle[] = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    const drift = (rand() - 0.5) * 0.06;
    const open = price;
    const close = Math.max(0.00001, open * (1 + drift));
    const high = Math.max(open, close) * (1 + rand() * 0.04);
    const low = Math.min(open, close) * (1 - rand() * 0.04);
    const volume = 50 + rand() * 1200;
    candles.push({
      t: now - i * 5 * 60_000,
      o: open,
      h: high,
      l: low,
      c: close,
      v: volume
    });
    price = close;
  }
  return candles;
}

export class MockTradingAdapter implements TradingAdapter {
  readonly id = "mock";
  readonly label = "Mock (discovery only)";
  readonly active = false;

  async getQuote(ctx: TradeContext, req: QuoteRequest): Promise<Quote> {
    const data = await this.getProjectMarketData(ctx);
    const price = safeNumber(data.priceAnm, 0.001);
    const slippageBps = req.slippageBps ?? 100;
    if (req.side === "BUY") {
      const inAnm = safeNumber(req.amountInAnm, 0);
      const fee = inAnm * FEE;
      const netIn = inAnm - fee;
      const out = netIn / Math.max(price, 1e-12);
      const min = out * (1 - slippageBps / 10_000);
      return {
        side: "BUY",
        inputAmount: inAnm.toString(),
        outputAmount: out.toFixed(6),
        priceAnm: price.toString(),
        feeAnm: fee.toFixed(6),
        minOutput: min.toFixed(6),
        routeLabel: "mock",
        expiresAt: new Date(Date.now() + 30_000).toISOString()
      };
    }
    const inTok = safeNumber(req.amountInToken, 0);
    const gross = inTok * price;
    const fee = gross * FEE;
    const out = gross - fee;
    return {
      side: "SELL",
      inputAmount: inTok.toString(),
      outputAmount: out.toFixed(6),
      priceAnm: price.toString(),
      feeAnm: fee.toFixed(6),
      minOutput: (out * (1 - slippageBps / 10_000)).toFixed(6),
      routeLabel: "mock",
      expiresAt: new Date(Date.now() + 30_000).toISOString()
    };
  }

  async createBuyTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new Error("Trading not active. This project is in discovery-only mode.");
  }
  async createSellTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new Error("Trading not active. This project is in discovery-only mode.");
  }

  async getProjectMarketData(ctx: TradeContext): Promise<MarketData> {
    const candles = buildCandles(`${ctx.projectId}:${ctx.symbol}`);
    const last = candles[candles.length - 1];
    const first = candles[0];
    const priceChange24h = ((last.c - first.c) / first.c) * 100;
    const volume24h = candles.reduce((a, c) => a + c.v, 0);
    return {
      candles,
      priceAnm: last.c.toFixed(8),
      priceChange24h,
      liquidityAnm: (last.c * 250_000).toFixed(2),
      volume24hAnm: volume24h.toFixed(2),
      marketCapAnm: (last.c * 1_000_000).toFixed(2)
    };
  }
}
