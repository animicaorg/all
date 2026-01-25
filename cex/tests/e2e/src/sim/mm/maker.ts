/**
 * Market Maker Core
 * 
 * Coordinates strategy execution, order placement, and state management.
 * Uses seeded RNG for deterministic behavior in tests.
 */

import { ExchangeAPIClient } from '../../http_client.js';
import { WSClient } from '../../ws_client.js';
import { InventoryManager, InventoryConfig } from './inventory.js';
import { RiskManager, RiskLimits } from './risk.js';
import { Strategy, createStrategy, OrderbookSnapshot } from './strategies.js';

export interface MarketMakerConfig {
  /** Market symbol (e.g., "BTC-USD") */
  market: string;
  /** Strategy name */
  strategy: 'tight_spread' | 'volatile' | 'inventory_skewed';
  /** Strategy-specific config */
  strategyConfig?: Record<string, any>;
  /** Inventory config */
  inventory: InventoryConfig;
  /** Risk limits */
  riskLimits: RiskLimits;
  /** Market parameters */
  tickSize: number;
  minOrderSize: number;
  /** Quote refresh interval (ms) */
  quoteRefreshInterval: number;
  /** Random seed for deterministic behavior */
  randomSeed?: number;
}

interface ActiveOrder {
  orderId: string;
  clientOrderId: string;
  side: 'buy' | 'sell';
  price: number;
  size: number;
  level: number;
}

/**
 * Seeded pseudo-random number generator
 */
class SeededRNG {
  private seed: number;
  
  constructor(seed: number) {
    this.seed = seed;
  }
  
  /** Generate random number [0, 1) */
  random(): number {
    const x = Math.sin(this.seed++) * 10000;
    return x - Math.floor(x);
  }
  
  /** Generate random integer [min, max) */
  randomInt(min: number, max: number): number {
    return Math.floor(this.random() * (max - min)) + min;
  }
}

/**
 * Market maker bot
 */
export class MarketMaker {
  private config: MarketMakerConfig;
  private httpClient: ExchangeAPIClient;
  private wsClient: WSClient;
  private inventoryManager: InventoryManager;
  private riskManager: RiskManager;
  private strategy: Strategy;
  private rng: SeededRNG;
  
  private running = false;
  private activeOrders = new Map<string, ActiveOrder>();
  private currentOrderbook?: OrderbookSnapshot;
  private quoteTimer?: NodeJS.Timeout;
  
  private stats = {
    quoteCycles: 0,
    ordersPlaced: 0,
    ordersCanceled: 0,
    trades: 0,
    riskBreaches: 0,
  };
  
  constructor(
    config: MarketMakerConfig,
    httpClient: ExchangeAPIClient,
    wsClient: WSClient
  ) {
    this.config = config;
    this.httpClient = httpClient;
    this.wsClient = wsClient;
    
    // Initialize managers
    this.inventoryManager = new InventoryManager(config.inventory);
    this.riskManager = new RiskManager(config.riskLimits, this.inventoryManager);
    
    // Initialize strategy
    this.strategy = createStrategy(
      config.strategy,
      {
        market: config.market,
        tickSize: config.tickSize,
        minOrderSize: config.minOrderSize,
        inventoryManager: this.inventoryManager,
      },
      config.strategyConfig
    );
    
    // Initialize RNG
    this.rng = new SeededRNG(config.randomSeed ?? Date.now());
    
    // Setup WebSocket handlers
    this.setupWSHandlers();
  }
  
  /**
   * Start market making
   */
  async start(): Promise<void> {
    if (this.running) {
      throw new Error('Market maker already running');
    }
    
    console.log(`[MM] Starting ${this.strategy.name} strategy on ${this.config.market}`);
    
    this.running = true;
    
    // Subscribe to orderbook and order updates
    this.wsClient.subscribeOrderbook(this.config.market);
    this.wsClient.subscribeOrders();
    
    // Start quote refresh cycle
    this.scheduleQuoteRefresh();
  }
  
  /**
   * Stop market making
   */
  async stop(): Promise<void> {
    console.log(`[MM] Stopping market maker on ${this.config.market}`);
    
    this.running = false;
    
    if (this.quoteTimer) {
      clearTimeout(this.quoteTimer);
      this.quoteTimer = undefined;
    }
    
    // Cancel all active orders
    await this.cancelAllOrders();
    
    // Unsubscribe
    this.wsClient.unsubscribe(`orderbook:${this.config.market}`);
    this.wsClient.unsubscribe('user:orders');
  }
  
  /**
   * Setup WebSocket event handlers
   */
  private setupWSHandlers(): void {
    // Orderbook updates
    this.wsClient.on('orderbook', (data: any) => {
      if (data.market === this.config.market) {
        this.currentOrderbook = {
          bids: data.data.bids || [],
          asks: data.data.asks || [],
        };
      }
    });
    
    // Order updates (fills, cancellations)
    this.wsClient.on('order', (data: any) => {
      this.handleOrderUpdate(data);
    });
  }
  
  /**
   * Handle order update from WebSocket
   */
  private handleOrderUpdate(update: any): void {
    const { orderId, status, side, executedQty, price } = update;
    
    // Update active orders
    if (status === 'filled' || status === 'partially_filled') {
      const order = this.activeOrders.get(orderId);
      if (order) {
        this.stats.trades++;
        
        // Update inventory
        const baseChange = side === 'buy' ? executedQty : -executedQty;
        const quoteChange = side === 'buy' ? -(executedQty * price) : (executedQty * price);
        this.inventoryManager.updateBalances(baseChange, quoteChange);
        
        console.log(`[MM] Trade executed: ${side} ${executedQty} @ ${price}`);
      }
    }
    
    if (status === 'filled' || status === 'canceled') {
      this.activeOrders.delete(orderId);
    }
  }
  
  /**
   * Schedule next quote refresh
   */
  private scheduleQuoteRefresh(): void {
    if (!this.running) return;
    
    // Add jitter to prevent thundering herd
    const jitter = this.rng.randomInt(-100, 100);
    const delay = this.config.quoteRefreshInterval + jitter;
    
    this.quoteTimer = setTimeout(() => {
      this.refreshQuotes().catch(err => {
        console.error(`[MM] Quote refresh error:`, err);
      }).finally(() => {
        this.scheduleQuoteRefresh();
      });
    }, delay);
  }
  
  /**
   * Refresh quotes
   */
  private async refreshQuotes(): Promise<void> {
    if (!this.currentOrderbook) {
      console.log(`[MM] Waiting for orderbook...`);
      return;
    }
    
    this.stats.quoteCycles++;
    
    const { bids, asks } = this.currentOrderbook;
    
    if (bids.length === 0 || asks.length === 0) {
      console.log(`[MM] Empty orderbook, skipping`);
      return;
    }
    
    const bestBid = bids[0].price;
    const bestAsk = asks[0].price;
    const midPrice = (bestBid + bestAsk) / 2;
    
    // Risk check
    const riskCheck = this.riskManager.checkRisk({ midPrice, bestBid, bestAsk });
    
    if (!riskCheck.passed) {
      this.stats.riskBreaches++;
      console.log(`[MM] Risk breach: ${riskCheck.reason}, action: ${riskCheck.action}`);
      
      if (riskCheck.action === 'cancel_all' || riskCheck.action === 'halt') {
        await this.cancelAllOrders();
        
        if (riskCheck.action === 'halt') {
          this.riskManager.halt();
          await this.stop();
        }
      }
      return;
    }
    
    // Generate new quotes
    const decision = this.strategy.generateQuotes(this.currentOrderbook);
    
    if (decision.quotes.bids.length === 0 && decision.quotes.asks.length === 0) {
      console.log(`[MM] No quotes generated: ${decision.reason}`);
      return;
    }
    
    // Cancel existing orders
    await this.cancelAllOrders();
    
    // Place new orders
    await this.placeQuotes(decision.quotes.bids, decision.quotes.asks);
  }
  
  /**
   * Place quote orders
   */
  private async placeQuotes(
    bids: Array<{ price: number; size: number }>,
    asks: Array<{ price: number; size: number }>
  ): Promise<void> {
    const orders = [
      ...bids.map((q, i) => ({ ...q, side: 'buy' as const, level: i })),
      ...asks.map((q, i) => ({ ...q, side: 'sell' as const, level: i })),
    ];
    
    for (const order of orders) {
      try {
        const clientOrderId = `mm_${Date.now()}_${this.rng.randomInt(0, 10000)}`;
        
        const response = await this.httpClient.placeLimitOrder({
          market: this.config.market,
          side: order.side,
          price: order.price.toFixed(8),
          size: order.size.toFixed(8),
          timeInForce: 'GTC',
          clientOrderId,
        });
        
        if (response.status === 200 && response.data.orderId) {
          this.activeOrders.set(response.data.orderId, {
            orderId: response.data.orderId,
            clientOrderId,
            side: order.side,
            price: order.price,
            size: order.size,
            level: order.level,
          });
          
          this.stats.ordersPlaced++;
        }
        
      } catch (error) {
        console.error(`[MM] Failed to place order:`, error);
      }
    }
  }
  
  /**
   * Cancel all active orders
   */
  private async cancelAllOrders(): Promise<void> {
    const orderIds = Array.from(this.activeOrders.keys());
    
    for (const orderId of orderIds) {
      try {
        await this.httpClient.cancelOrder(orderId);
        this.activeOrders.delete(orderId);
        this.stats.ordersCanceled++;
      } catch (error) {
        console.error(`[MM] Failed to cancel order ${orderId}:`, error);
      }
    }
  }
  
  /**
   * Get current statistics
   */
  getStats() {
    const balances = this.inventoryManager.getBalances();
    const midPrice = this.currentOrderbook 
      ? (this.currentOrderbook.bids[0]?.price + this.currentOrderbook.asks[0]?.price) / 2
      : 0;
    const inventory = midPrice > 0 ? this.inventoryManager.getSnapshot(midPrice) : null;
    
    return {
      ...this.stats,
      activeOrders: this.activeOrders.size,
      balances,
      inventory,
      running: this.running,
      halted: this.riskManager.isHalted(),
    };
  }
  
  /**
   * Reset statistics
   */
  resetStats(): void {
    this.stats = {
      quoteCycles: 0,
      ordersPlaced: 0,
      ordersCanceled: 0,
      trades: 0,
      riskBreaches: 0,
    };
  }
}
