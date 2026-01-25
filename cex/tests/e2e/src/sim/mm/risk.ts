/**
 * Risk Management
 * 
 * Monitors positions, exposure, and market conditions.
 * Triggers risk actions like canceling orders or halting trading.
 */

import { InventoryManager } from './inventory.js';

export interface RiskLimits {
  /** Maximum base position size */
  maxBasePosition: number;
  /** Maximum quote exposure */
  maxQuoteExposure: number;
  /** Maximum spread before halting (basis points) */
  maxSpreadBps: number;
  /** Maximum price deviation from reference (percentage) */
  maxPriceDeviationPercent: number;
  /** Maximum inventory skew before reducing quotes */
  maxInventorySkew: number;
}

export interface RiskMetrics {
  basePosition: number;
  quoteExposure: number;
  inventorySkew: number;
  currentSpread: number;
  priceDeviation: number;
}

export interface RiskCheck {
  passed: boolean;
  reason?: string;
  action?: 'cancel_all' | 'reduce_size' | 'widen_spread' | 'halt';
  metrics: RiskMetrics;
}

/**
 * Manages risk checks and limit enforcement
 */
export class RiskManager {
  private limits: RiskLimits;
  private inventoryManager: InventoryManager;
  private referencePrice?: number;
  private halted = false;
  
  constructor(limits: RiskLimits, inventoryManager: InventoryManager) {
    this.limits = limits;
    this.inventoryManager = inventoryManager;
  }
  
  /**
   * Perform comprehensive risk check
   */
  checkRisk(params: {
    midPrice: number;
    bestBid: number;
    bestAsk: number;
  }): RiskCheck {
    const { midPrice, bestBid, bestAsk } = params;
    
    // Update reference price if not set
    if (!this.referencePrice) {
      this.referencePrice = midPrice;
    }
    
    const inventory = this.inventoryManager.getSnapshot(midPrice);
    const spread = ((bestAsk - bestBid) / bestBid) * 10000; // in bps
    const priceDeviation = Math.abs(midPrice - this.referencePrice) / this.referencePrice * 100;
    
    const metrics: RiskMetrics = {
      basePosition: Math.abs(inventory.base),
      quoteExposure: Math.abs(inventory.baseValue),
      inventorySkew: Math.abs(inventory.skew),
      currentSpread: spread,
      priceDeviation,
    };
    
    // Check if halted
    if (this.halted) {
      return {
        passed: false,
        reason: 'trading_halted',
        action: 'halt',
        metrics,
      };
    }
    
    // Check position limits
    if (metrics.basePosition > this.limits.maxBasePosition) {
      return {
        passed: false,
        reason: 'max_base_position_exceeded',
        action: 'cancel_all',
        metrics,
      };
    }
    
    // Check exposure limits
    if (metrics.quoteExposure > this.limits.maxQuoteExposure) {
      return {
        passed: false,
        reason: 'max_quote_exposure_exceeded',
        action: 'cancel_all',
        metrics,
      };
    }
    
    // Check spread
    if (spread > this.limits.maxSpreadBps) {
      return {
        passed: false,
        reason: 'spread_too_wide',
        action: 'halt',
        metrics,
      };
    }
    
    // Check price deviation
    if (priceDeviation > this.limits.maxPriceDeviationPercent) {
      return {
        passed: false,
        reason: 'price_deviation_exceeded',
        action: 'cancel_all',
        metrics,
      };
    }
    
    // Check inventory skew (warning, not hard limit)
    if (metrics.inventorySkew > this.limits.maxInventorySkew) {
      return {
        passed: true,
        reason: 'high_inventory_skew',
        action: 'reduce_size',
        metrics,
      };
    }
    
    return {
      passed: true,
      metrics,
    };
  }
  
  /**
   * Check if we should reduce quote sizes
   */
  shouldReduceSize(inventorySkew: number): boolean {
    return Math.abs(inventorySkew) > this.limits.maxInventorySkew * 0.7;
  }
  
  /**
   * Calculate size multiplier based on inventory skew
   * Returns value between 0.5 and 1.0
   */
  getSizeMultiplier(inventorySkew: number): number {
    const absSkew = Math.abs(inventorySkew);
    const threshold = this.limits.maxInventorySkew * 0.7;
    
    if (absSkew < threshold) {
      return 1.0;
    }
    
    // Linear reduction from 1.0 to 0.5
    const excessSkew = absSkew - threshold;
    const maxExcess = this.limits.maxInventorySkew - threshold;
    const reduction = (excessSkew / maxExcess) * 0.5;
    
    return Math.max(0.5, 1.0 - reduction);
  }
  
  /**
   * Halt trading
   */
  halt(): void {
    this.halted = true;
  }
  
  /**
   * Resume trading
   */
  resume(): void {
    this.halted = false;
  }
  
  /**
   * Check if trading is halted
   */
  isHalted(): boolean {
    return this.halted;
  }
  
  /**
   * Update reference price
   */
  updateReferencePrice(price: number): void {
    this.referencePrice = price;
  }
  
  /**
   * Get current risk metrics
   */
  getMetrics(midPrice: number): RiskMetrics {
    const inventory = this.inventoryManager.getSnapshot(midPrice);
    
    return {
      basePosition: Math.abs(inventory.base),
      quoteExposure: Math.abs(inventory.baseValue),
      inventorySkew: Math.abs(inventory.skew),
      currentSpread: 0, // Needs orderbook
      priceDeviation: this.referencePrice ? 
        Math.abs(midPrice - this.referencePrice) / this.referencePrice * 100 : 0,
    };
  }
  
  /**
   * Reset risk manager state
   */
  reset(): void {
    this.referencePrice = undefined;
    this.halted = false;
  }
}
