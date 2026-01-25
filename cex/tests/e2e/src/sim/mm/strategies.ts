/**
 * Market Making Strategies
 * 
 * Implements different market making approaches:
 * - TightSpreadStrategy: Aggressive quoting near mid price
 * - VolatileStrategy: Wider spreads during volatility
 * - InventorySkewedStrategy: Adjusts quotes based on inventory
 */

import { Quote, QuoteLadder, generateQuoteLadder, calculateSpreadBps } from './quoting.js';
import { InventoryManager } from './inventory.js';

export interface StrategyParams {
  market: string;
  tickSize: number;
  minOrderSize: number;
  inventoryManager: InventoryManager;
}

export interface OrderbookSnapshot {
  bids: Array<{ price: number; size: number }>;
  asks: Array<{ price: number; size: number }>;
}

export interface QuoteDecision {
  quotes: QuoteLadder;
  reason: string;
  metadata?: Record<string, any>;
}

/**
 * Base strategy interface
 */
export interface Strategy {
  name: string;
  generateQuotes(orderbook: OrderbookSnapshot): QuoteDecision;
}

/**
 * Tight Spread Strategy
 * 
 * Provides liquidity with tight spreads around the mid price.
 * Best for stable markets with low volatility.
 */
export class TightSpreadStrategy implements Strategy {
  name = 'tight_spread';
  
  private params: StrategyParams;
  private spreadBps: number;
  private levels: number;
  
  constructor(
    params: StrategyParams,
    config: {
      spreadBps?: number;
      levels?: number;
    } = {}
  ) {
    this.params = params;
    this.spreadBps = config.spreadBps ?? 10; // 0.1%
    this.levels = config.levels ?? 3;
  }
  
  generateQuotes(orderbook: OrderbookSnapshot): QuoteDecision {
    const bestBid = orderbook.bids[0]?.price;
    const bestAsk = orderbook.asks[0]?.price;
    
    if (!bestBid || !bestAsk) {
      return {
        quotes: { bids: [], asks: [] },
        reason: 'insufficient_orderbook',
      };
    }
    
    const midPrice = (bestBid + bestAsk) / 2;
    const inventory = this.params.inventoryManager.getSnapshot(midPrice);
    
    // Generate tight quotes with minimal inventory adjustment
    const quotes = generateQuoteLadder({
      midPrice,
      levels: this.levels,
      baseSpreadBps: this.spreadBps,
      levelSpreadIncrement: 5, // 0.05% per level
      inventorySkew: inventory.skew,
      skewSensitivity: 0.2, // Low sensitivity
      baseSize: this.params.minOrderSize * 10,
      sizeDecrement: 0.8,
      tickSize: this.params.tickSize,
    });
    
    return {
      quotes,
      reason: 'tight_spread',
      metadata: {
        midPrice,
        inventorySkew: inventory.skew,
        levels: this.levels,
      },
    };
  }
}

/**
 * Volatile Strategy
 * 
 * Widens spreads during high volatility to protect against
 * adverse selection. Monitors recent price changes.
 */
export class VolatileStrategy implements Strategy {
  name = 'volatile';
  
  private params: StrategyParams;
  private baseSpreadBps: number;
  private levels: number;
  private priceHistory: number[] = [];
  private maxHistorySize: number;
  
  constructor(
    params: StrategyParams,
    config: {
      baseSpreadBps?: number;
      levels?: number;
      maxHistorySize?: number;
    } = {}
  ) {
    this.params = params;
    this.baseSpreadBps = config.baseSpreadBps ?? 20; // 0.2%
    this.levels = config.levels ?? 5;
    this.maxHistorySize = config.maxHistorySize ?? 100;
  }
  
  generateQuotes(orderbook: OrderbookSnapshot): QuoteDecision {
    const bestBid = orderbook.bids[0]?.price;
    const bestAsk = orderbook.asks[0]?.price;
    
    if (!bestBid || !bestAsk) {
      return {
        quotes: { bids: [], asks: [] },
        reason: 'insufficient_orderbook',
      };
    }
    
    const midPrice = (bestBid + bestAsk) / 2;
    
    // Track price history
    this.priceHistory.push(midPrice);
    if (this.priceHistory.length > this.maxHistorySize) {
      this.priceHistory.shift();
    }
    
    // Calculate volatility (standard deviation of returns)
    const volatility = this.calculateVolatility();
    
    // Widen spread based on volatility
    const volatilityMultiplier = 1 + (volatility * 10); // Scale volatility
    const adjustedSpread = this.baseSpreadBps * volatilityMultiplier;
    
    const inventory = this.params.inventoryManager.getSnapshot(midPrice);
    
    const quotes = generateQuoteLadder({
      midPrice,
      levels: this.levels,
      baseSpreadBps: adjustedSpread,
      levelSpreadIncrement: 10, // 0.1% per level
      inventorySkew: inventory.skew,
      skewSensitivity: 0.3,
      baseSize: this.params.minOrderSize * 8,
      sizeDecrement: 0.75,
      tickSize: this.params.tickSize,
    });
    
    return {
      quotes,
      reason: 'volatile_adjusted',
      metadata: {
        midPrice,
        volatility,
        volatilityMultiplier,
        adjustedSpread,
      },
    };
  }
  
  private calculateVolatility(): number {
    if (this.priceHistory.length < 2) {
      return 0;
    }
    
    // Calculate returns
    const returns: number[] = [];
    for (let i = 1; i < this.priceHistory.length; i++) {
      const ret = (this.priceHistory[i] - this.priceHistory[i - 1]) / this.priceHistory[i - 1];
      returns.push(ret);
    }
    
    // Calculate mean
    const mean = returns.reduce((sum, r) => sum + r, 0) / returns.length;
    
    // Calculate variance
    const variance = returns.reduce((sum, r) => sum + Math.pow(r - mean, 2), 0) / returns.length;
    
    return Math.sqrt(variance);
  }
}

/**
 * Inventory Skewed Strategy
 * 
 * Heavily adjusts quotes based on inventory position to
 * push inventory back toward target levels.
 */
export class InventorySkewedStrategy implements Strategy {
  name = 'inventory_skewed';
  
  private params: StrategyParams;
  private spreadBps: number;
  private levels: number;
  private skewSensitivity: number;
  
  constructor(
    params: StrategyParams,
    config: {
      spreadBps?: number;
      levels?: number;
      skewSensitivity?: number;
    } = {}
  ) {
    this.params = params;
    this.spreadBps = config.spreadBps ?? 15; // 0.15%
    this.levels = config.levels ?? 4;
    this.skewSensitivity = config.skewSensitivity ?? 0.8; // High sensitivity
  }
  
  generateQuotes(orderbook: OrderbookSnapshot): QuoteDecision {
    const bestBid = orderbook.bids[0]?.price;
    const bestAsk = orderbook.asks[0]?.price;
    
    if (!bestBid || !bestAsk) {
      return {
        quotes: { bids: [], asks: [] },
        reason: 'insufficient_orderbook',
      };
    }
    
    const midPrice = (bestBid + bestAsk) / 2;
    const inventory = this.params.inventoryManager.getSnapshot(midPrice);
    
    // Aggressive skew adjustment to manage inventory
    // If we have too much base (positive skew), we widen asks less and bids more
    // If we have too little base (negative skew), we widen bids less and asks more
    const quotes = generateQuoteLadder({
      midPrice,
      levels: this.levels,
      baseSpreadBps: this.spreadBps,
      levelSpreadIncrement: 8,
      inventorySkew: inventory.skew,
      skewSensitivity: this.skewSensitivity,
      baseSize: this.params.minOrderSize * 12,
      sizeDecrement: 0.85,
      tickSize: this.params.tickSize,
    });
    
    // Filter out quotes that violate inventory limits
    const filteredBids = quotes.bids.filter(bid =>
      this.params.inventoryManager.canBuy(bid.size, bid.price)
    );
    
    const filteredAsks = quotes.asks.filter(ask =>
      this.params.inventoryManager.canSell(ask.size)
    );
    
    return {
      quotes: {
        bids: filteredBids,
        asks: filteredAsks,
      },
      reason: 'inventory_skewed',
      metadata: {
        midPrice,
        inventorySkew: inventory.skew,
        inventoryRatio: inventory.ratio,
        targetRatio: 0.5,
        filteredBids: quotes.bids.length - filteredBids.length,
        filteredAsks: quotes.asks.length - filteredAsks.length,
      },
    };
  }
}

/**
 * Create strategy by name
 */
export function createStrategy(
  name: string,
  params: StrategyParams,
  config?: Record<string, any>
): Strategy {
  switch (name) {
    case 'tight_spread':
      return new TightSpreadStrategy(params, config);
    case 'volatile':
      return new VolatileStrategy(params, config);
    case 'inventory_skewed':
      return new InventorySkewedStrategy(params, config);
    default:
      throw new Error(`Unknown strategy: ${name}`);
  }
}
