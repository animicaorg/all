import type { MarketData, Quote, QuoteRequest, TransactionRequest } from "@launchpad/shared";
import type { TradeContext, TradingAdapter } from "./types";
import { TradingError } from "./types";

/**
 * Animica native trading adapter (placeholder).
 *
 * Wire this to whichever Animica-native trading surface ships first
 * (DA pool routes, Animica asset transfers, etc). Until then it
 * surfaces a clear "not active" error so the UI can render the
 * discovery-only notice instead of executing trades.
 */
export class AnimicaNativeTradingAdapter implements TradingAdapter {
  readonly id = "animica-native";
  readonly label = "Animica Native";
  readonly active = false;

  async getQuote(_ctx: TradeContext, _req: QuoteRequest): Promise<Quote> {
    throw new TradingError("NOT_ACTIVE", "Animica-native trading is not active yet.");
  }
  async createBuyTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new TradingError("NOT_ACTIVE", "Animica-native trading is not active yet.");
  }
  async createSellTransaction(_ctx: TradeContext, _req: QuoteRequest): Promise<TransactionRequest> {
    throw new TradingError("NOT_ACTIVE", "Animica-native trading is not active yet.");
  }
  async getProjectMarketData(_ctx: TradeContext): Promise<MarketData> {
    throw new TradingError("NOT_ACTIVE", "Animica-native market data is not active yet.");
  }
}
