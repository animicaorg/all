/**
 * Inventory Management
 * 
 * Tracks market maker's base and quote asset positions,
 * calculates position limits, and computes inventory skew
 * for quote adjustment.
 */

export interface InventoryConfig {
  /** Initial base asset amount */
  initialBase: number;
  /** Initial quote asset amount */
  initialQuote: number;
  /** Maximum base asset position (absolute value) */
  maxBasePosition: number;
  /** Maximum quote asset exposure */
  maxQuoteExposure: number;
  /** Target inventory ratio (0.5 = balanced) */
  targetRatio: number;
}

export interface InventorySnapshot {
  base: number;
  quote: number;
  baseValue: number; // in quote terms
  totalValue: number;
  ratio: number; // base_value / total_value
  skew: number; // deviation from target (-1 to 1)
}

/**
 * Manages inventory tracking and position limits
 */
export class InventoryManager {
  private baseBalance: number;
  private quoteBalance: number;
  private config: InventoryConfig;
  
  constructor(config: InventoryConfig) {
    this.config = config;
    this.baseBalance = config.initialBase;
    this.quoteBalance = config.initialQuote;
  }
  
  /**
   * Update balances after a trade
   */
  updateBalances(baseChange: number, quoteChange: number): void {
    this.baseBalance += baseChange;
    this.quoteBalance += quoteChange;
  }
  
  /**
   * Get current inventory snapshot
   */
  getSnapshot(midPrice: number): InventorySnapshot {
    const baseValue = this.baseBalance * midPrice;
    const totalValue = baseValue + this.quoteBalance;
    const ratio = totalValue > 0 ? baseValue / totalValue : 0.5;
    const skew = (ratio - this.config.targetRatio) / (1 - this.config.targetRatio);
    
    return {
      base: this.baseBalance,
      quote: this.quoteBalance,
      baseValue,
      totalValue,
      ratio,
      skew: Math.max(-1, Math.min(1, skew)),
    };
  }
  
  /**
   * Calculate inventory skew coefficient for quote adjustment
   * Returns a value between -1 and 1:
   * - Negative when we have too much base (should sell)
   * - Positive when we have too little base (should buy)
   */
  calculateSkew(midPrice: number): number {
    const snapshot = this.getSnapshot(midPrice);
    return snapshot.skew;
  }
  
  /**
   * Check if we can place a buy order (would increase base)
   */
  canBuy(size: number, price: number): boolean {
    const newBase = this.baseBalance + size;
    const requiredQuote = size * price;
    
    // Check if we have enough quote
    if (this.quoteBalance < requiredQuote) {
      return false;
    }
    
    // Check position limits
    if (Math.abs(newBase) > this.config.maxBasePosition) {
      return false;
    }
    
    return true;
  }
  
  /**
   * Check if we can place a sell order (would decrease base)
   */
  canSell(size: number): boolean {
    const newBase = this.baseBalance - size;
    
    // Check if we have enough base
    if (this.baseBalance < size) {
      return false;
    }
    
    // Check position limits
    if (Math.abs(newBase) > this.config.maxBasePosition) {
      return false;
    }
    
    return true;
  }
  
  /**
   * Check if within risk limits
   */
  isWithinLimits(midPrice: number): boolean {
    const baseValue = Math.abs(this.baseBalance * midPrice);
    return baseValue <= this.config.maxQuoteExposure;
  }
  
  /**
   * Get current balances
   */
  getBalances(): { base: number; quote: number } {
    return {
      base: this.baseBalance,
      quote: this.quoteBalance,
    };
  }
  
  /**
   * Reset balances to initial state
   */
  reset(): void {
    this.baseBalance = this.config.initialBase;
    this.quoteBalance = this.config.initialQuote;
  }
}
