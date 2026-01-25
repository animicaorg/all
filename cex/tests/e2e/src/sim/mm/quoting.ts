/**
 * Quote Generation
 * 
 * Generates bid/ask prices and sizes for market making.
 * Supports multiple quote levels (ladder), inventory skew,
 * and dynamic spread adjustment.
 */

export interface QuoteParams {
  /** Mid price (typically from orderbook) */
  midPrice: number;
  /** Base spread in basis points */
  spreadBps: number;
  /** Inventory skew adjustment (-1 to 1) */
  inventorySkew: number;
  /** Skew sensitivity (how much inventory affects quotes) */
  skewSensitivity: number;
  /** Order size for this level */
  size: number;
}

export interface Quote {
  price: number;
  size: number;
  side: 'buy' | 'sell';
}

export interface QuoteLadder {
  bids: Quote[];
  asks: Quote[];
}

/**
 * Calculate bid/ask quotes with inventory skew
 */
export function calculateQuotes(params: QuoteParams): { bid: Quote; ask: Quote } {
  const { midPrice, spreadBps, inventorySkew, skewSensitivity, size } = params;
  
  // Base spread in decimal
  const baseSpread = spreadBps / 10000;
  
  // Adjust spread based on inventory skew
  // Positive skew = too much base, widen ask and tighten bid
  // Negative skew = too little base, tighten ask and widen bid
  const skewAdjustment = inventorySkew * skewSensitivity * baseSpread;
  
  const bidSpread = baseSpread - skewAdjustment;
  const askSpread = baseSpread + skewAdjustment;
  
  // Calculate prices
  const bidPrice = midPrice * (1 - bidSpread);
  const askPrice = midPrice * (1 + askSpread);
  
  return {
    bid: {
      price: bidPrice,
      size,
      side: 'buy',
    },
    ask: {
      price: askPrice,
      size,
      side: 'sell',
    },
  };
}

/**
 * Generate a multi-level quote ladder
 */
export function generateQuoteLadder(params: {
  midPrice: number;
  levels: number;
  baseSpreadBps: number;
  levelSpreadIncrement: number; // Additional spread per level
  inventorySkew: number;
  skewSensitivity: number;
  baseSize: number;
  sizeDecrement: number; // Size multiplier per level (e.g., 0.8 = 80% of previous)
  tickSize: number;
}): QuoteLadder {
  const {
    midPrice,
    levels,
    baseSpreadBps,
    levelSpreadIncrement,
    inventorySkew,
    skewSensitivity,
    baseSize,
    sizeDecrement,
    tickSize,
  } = params;
  
  const bids: Quote[] = [];
  const asks: Quote[] = [];
  
  for (let i = 0; i < levels; i++) {
    const spreadBps = baseSpreadBps + (i * levelSpreadIncrement);
    const size = baseSize * Math.pow(sizeDecrement, i);
    
    const quotes = calculateQuotes({
      midPrice,
      spreadBps,
      inventorySkew,
      skewSensitivity,
      size,
    });
    
    // Round to tick size
    quotes.bid.price = roundToTickSize(quotes.bid.price, tickSize);
    quotes.ask.price = roundToTickSize(quotes.ask.price, tickSize);
    
    bids.push(quotes.bid);
    asks.push(quotes.ask);
  }
  
  return { bids, asks };
}

/**
 * Round price to tick size
 */
export function roundToTickSize(price: number, tickSize: number): number {
  return Math.round(price / tickSize) * tickSize;
}

/**
 * Calculate mid price from orderbook
 */
export function calculateMidPrice(
  bestBid: number | undefined,
  bestAsk: number | undefined,
  fallbackPrice: number = 100
): number {
  if (bestBid !== undefined && bestAsk !== undefined) {
    return (bestBid + bestAsk) / 2;
  }
  if (bestBid !== undefined) {
    return bestBid * 1.001; // 0.1% above bid
  }
  if (bestAsk !== undefined) {
    return bestAsk * 0.999; // 0.1% below ask
  }
  return fallbackPrice;
}

/**
 * Calculate orderbook spread in basis points
 */
export function calculateSpreadBps(bestBid: number, bestAsk: number): number {
  const spread = (bestAsk - bestBid) / bestBid;
  return spread * 10000;
}

/**
 * Check if quote is crossed (bid >= ask)
 */
export function isQuoteCrossed(bid: number, ask: number): boolean {
  return bid >= ask;
}

/**
 * Validate quote prices are reasonable
 */
export function validateQuote(quote: Quote, midPrice: number, maxDeviationPercent: number): boolean {
  const deviation = Math.abs(quote.price - midPrice) / midPrice;
  return deviation <= maxDeviationPercent / 100;
}
