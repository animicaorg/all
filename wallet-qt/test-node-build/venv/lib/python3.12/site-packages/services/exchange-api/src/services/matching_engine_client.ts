/**
 * Matching Engine Client
 * 
 * Interfaces with the order matching engine to submit/cancel orders
 * and receive real-time market events (trades, orderbook updates).
 */

import { Logger } from '../utils/logger.js';

export enum OrderSide {
  BUY = 'BUY',
  SELL = 'SELL',
}

export enum OrderType {
  LIMIT = 'LIMIT',
  MARKET = 'MARKET',
}

export enum OrderStatus {
  PENDING = 'PENDING',
  OPEN = 'OPEN',
  PARTIALLY_FILLED = 'PARTIALLY_FILLED',
  FILLED = 'FILLED',
  CANCELLED = 'CANCELLED',
  REJECTED = 'REJECTED',
}

export interface OrderSubmitRequest {
  userId: string;
  market: string;
  side: OrderSide;
  type: OrderType;
  price?: string; // Required for LIMIT orders
  quantity: string;
  clientOrderId?: string;
  timeInForce?: 'GTC' | 'IOC' | 'FOK';
}

export interface OrderCancelRequest {
  orderId: string;
  userId: string;
  market: string;
}

export interface OrderSubmitResponse {
  orderId: string;
  status: OrderStatus;
  message?: string;
}

export interface OrderCancelResponse {
  orderId: string;
  status: OrderStatus;
  message?: string;
}

export interface TradeEvent {
  type: 'trade';
  tradeId: string;
  market: string;
  price: string;
  quantity: string;
  buyOrderId: string;
  sellOrderId: string;
  buyUserId: string;
  sellUserId: string;
  timestamp: number;
}

export interface OrderBookEvent {
  type: 'orderbook';
  market: string;
  sequence: number;
  bids?: Array<{ price: string; quantity: string }>;
  asks?: Array<{ price: string; quantity: string }>;
  timestamp: number;
}

export interface OrderStatusEvent {
  type: 'order_status';
  orderId: string;
  userId: string;
  status: OrderStatus;
  filledQuantity: string;
  remainingQuantity: string;
  timestamp: number;
}

export type MatchingEngineEvent = TradeEvent | OrderBookEvent | OrderStatusEvent;

export interface MatchingEngineConfig {
  // TODO: Add configuration options
  // For NATS: server URLs, credentials
  // For HTTP: base URL, timeout, retry policy
  enabled: boolean;
  type: 'nats' | 'http' | 'mock';
}

export class MatchingEngineClient {
  private logger: Logger;
  private config: MatchingEngineConfig;
  private eventHandlers: Map<string, Set<(event: MatchingEngineEvent) => void>> = new Map();

  constructor(config: MatchingEngineConfig, logger: Logger) {
    this.config = config;
    this.logger = logger;
  }

  /**
   * Initialize the client and establish connections
   */
  async initialize(): Promise<void> {
    // TODO: Initialize connection based on config.type
    // - For NATS: connect to NATS server, subscribe to topics
    // - For HTTP: validate endpoint connectivity
    // - For mock: no-op
    this.logger.info('Matching engine client initialized', { type: this.config.type });
  }

  /**
   * Submit a new order to the matching engine
   */
  async submitOrder(request: OrderSubmitRequest): Promise<OrderSubmitResponse> {
    // TODO: Implement order submission
    // - For NATS: publish to order submission topic, wait for ack
    // - For HTTP: POST to /orders endpoint
    // - For mock: return mock response
    this.logger.info('Order submitted', { request });

    return {
      orderId: `mock-order-${Date.now()}`,
      status: OrderStatus.PENDING,
      message: 'Order submitted (mock)',
    };
  }

  /**
   * Cancel an existing order
   */
  async cancelOrder(request: OrderCancelRequest): Promise<OrderCancelResponse> {
    // TODO: Implement order cancellation
    // - For NATS: publish to order cancellation topic, wait for ack
    // - For HTTP: DELETE to /orders/:orderId endpoint
    // - For mock: return mock response
    this.logger.info('Order cancelled', { request });

    return {
      orderId: request.orderId,
      status: OrderStatus.CANCELLED,
      message: 'Order cancelled (mock)',
    };
  }

  /**
   * Subscribe to market events for a specific market
   */
  subscribeToMarket(market: string, handler: (event: MatchingEngineEvent) => void): void {
    // TODO: Implement market event subscription
    // - For NATS: subscribe to market-specific topics (trades, orderbook)
    // - For HTTP: establish WebSocket connection or SSE
    // - For mock: no-op or simulate events
    if (!this.eventHandlers.has(market)) {
      this.eventHandlers.set(market, new Set());
    }
    this.eventHandlers.get(market)!.add(handler);
    this.logger.info('Subscribed to market', { market });
  }

  /**
   * Unsubscribe from market events
   */
  unsubscribeFromMarket(market: string, handler: (event: MatchingEngineEvent) => void): void {
    const handlers = this.eventHandlers.get(market);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        this.eventHandlers.delete(market);
      }
    }
    this.logger.info('Unsubscribed from market', { market });
  }

  /**
   * Close all connections and cleanup
   */
  async shutdown(): Promise<void> {
    // TODO: Cleanup connections
    // - For NATS: close connection
    // - For HTTP/WebSocket: close connections
    // - For mock: no-op
    this.eventHandlers.clear();
    this.logger.info('Matching engine client shut down');
  }
}
