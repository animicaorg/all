import type { MarketData, Quote, QuoteRequest, TransactionRequest } from "@launchpad/shared";

export interface TradeContext {
  projectId: string;
  projectSlug: string;
  symbol: string;
  creatorWallet?: string;
  buyerWallet?: string;
  poolAddress?: string | null;
  initialSupply?: string | null;
}

export interface TradingAdapter {
  readonly id: string;
  readonly label: string;
  readonly active: boolean;
  getQuote(ctx: TradeContext, req: QuoteRequest): Promise<Quote>;
  createBuyTransaction(ctx: TradeContext, req: QuoteRequest): Promise<TransactionRequest>;
  createSellTransaction(ctx: TradeContext, req: QuoteRequest): Promise<TransactionRequest>;
  getProjectMarketData(ctx: TradeContext): Promise<MarketData>;
}

export class TradingError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}
