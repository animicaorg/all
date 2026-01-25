/**
 * WebSocket Protocol Definitions
 * 
 * Defines all message types exchanged between client and server.
 * Uses discriminated unions for type-safe message handling.
 */

/**
 * Channel configuration for subscriptions
 */
export interface ChannelConfig {
  /** Channel name: book, trades, tickers, candles, orders, balances */
  name: string;
  /** Market symbol (e.g., "BTC_USD") - required for market-specific channels */
  market?: string;
  /** Candle interval (e.g., "1m", "5m", "1h") - required for candles channel */
  interval?: string;
}

/**
 * Client -> Server Messages
 */

/** Authenticate with API key and signature */
export interface AuthMessage {
  op: 'auth';
  apiKey: string;
  timestamp: number;
  nonce: string;
  signature: string;
}

/** Subscribe to one or more channels */
export interface SubscribeMessage {
  op: 'subscribe';
  id: number;
  channels: ChannelConfig[];
}

/** Unsubscribe from one or more channels */
export interface UnsubscribeMessage {
  op: 'unsubscribe';
  id: number;
  channels: ChannelConfig[];
}

/** Client heartbeat ping */
export interface PingMessage {
  op: 'ping';
  ts: number;
}

/** Union type for all client messages */
export type ClientMessage =
  | AuthMessage
  | SubscribeMessage
  | UnsubscribeMessage
  | PingMessage;

/**
 * Server -> Client Messages
 */

/** Server heartbeat pong response */
export interface PongMessage {
  op: 'pong';
  ts: number;
}

/** Subscription confirmation */
export interface SubscribedMessage {
  op: 'subscribed';
  id: number;
  channels: ChannelConfig[];
}

/** Unsubscription confirmation */
export interface UnsubscribedMessage {
  op: 'unsubscribed';
  id: number;
  channels: ChannelConfig[];
}

/** Orderbook snapshot message */
export interface OrderbookSnapshotMessage {
  type: 'snapshot';
  channel: 'book';
  market: string;
  seq: number;
  bids: [string, string][]; // [price, size]
  asks: [string, string][]; // [price, size]
  ts: number;
}

/** Orderbook incremental update message */
export interface OrderbookUpdateMessage {
  type: 'update';
  channel: 'book';
  market: string;
  seq: number;
  changes: {
    bids?: [string, string][]; // [price, size] - size=0 means remove
    asks?: [string, string][]; // [price, size] - size=0 means remove
  };
  ts: number;
}

/** Trade execution message */
export interface TradeMessage {
  type: 'trade';
  market: string;
  trade_id: string;
  price: string;
  size: string;
  side: 'buy' | 'sell';
  ts: number;
}

/** Ticker update message */
export interface TickerMessage {
  type: 'ticker';
  market: string;
  last: string;
  bid: string;
  ask: string;
  volume: string;
  high?: string;
  low?: string;
  change?: string;
  change_percent?: string;
  ts: number;
}

/** Candle data message */
export interface CandleMessage {
  type: 'candle';
  market: string;
  interval: string;
  open: string;
  high: string;
  low: string;
  close: string;
  volume: string;
  ts: number;
}

/** Order update message (private) */
export interface OrderMessage {
  type: 'order';
  order_id: string;
  client_order_id?: string;
  market: string;
  side: 'buy' | 'sell';
  order_type: 'limit' | 'market';
  price?: string;
  size: string;
  filled: string;
  remaining: string;
  status: 'open' | 'partial' | 'filled' | 'cancelled';
  ts: number;
}

/** Balance update message (private) */
export interface BalanceMessage {
  type: 'balance';
  asset: string;
  available: string;
  locked: string;
  total: string;
  ts: number;
}

/** Error message */
export interface ErrorMessage {
  type: 'error';
  code: string;
  message: string;
  id?: number;
}

/** Union type for all data messages */
export type DataMessage =
  | OrderbookSnapshotMessage
  | OrderbookUpdateMessage
  | TradeMessage
  | TickerMessage
  | CandleMessage
  | OrderMessage
  | BalanceMessage
  | ErrorMessage;

/** Union type for all server messages */
export type ServerMessage =
  | PongMessage
  | SubscribedMessage
  | UnsubscribedMessage
  | DataMessage;

/**
 * Message priority levels for backpressure handling
 */
export enum MessagePriority {
  CRITICAL = 0, // Snapshots, auth responses, error messages
  HIGH = 1, // Orderbook updates, order updates, balance updates
  NORMAL = 2, // Trades
  LOW = 3, // Tickers, candles
}

/**
 * Channel types
 */
export const CHANNEL_TYPES = {
  BOOK: 'book',
  TRADES: 'trades',
  TICKERS: 'tickers',
  CANDLES: 'candles',
  ORDERS: 'orders',
  BALANCES: 'balances',
} as const;

/**
 * Private channels that require authentication
 */
export const PRIVATE_CHANNELS = [
  CHANNEL_TYPES.ORDERS,
  CHANNEL_TYPES.BALANCES,
] as const;

/**
 * WebSocket close codes
 */
export const WS_CLOSE_CODES = {
  NORMAL: 1000,
  GOING_AWAY: 1001,
  PROTOCOL_ERROR: 1002,
  UNSUPPORTED_DATA: 1003,
  POLICY_VIOLATION: 1008,
  MESSAGE_TOO_BIG: 1009,
  TRY_AGAIN_LATER: 1013, // Service overload / backpressure
  INTERNAL_ERROR: 1011,
} as const;

/**
 * Error codes
 */
export const ERROR_CODES = {
  AUTH_REQUIRED: 'AUTH_REQUIRED',
  AUTH_FAILED: 'AUTH_FAILED',
  INVALID_MESSAGE: 'INVALID_MESSAGE',
  INVALID_CHANNEL: 'INVALID_CHANNEL',
  TOO_MANY_SUBSCRIPTIONS: 'TOO_MANY_SUBSCRIPTIONS',
  SEQUENCE_GAP: 'SEQUENCE_GAP',
  RATE_LIMIT: 'RATE_LIMIT',
  INTERNAL_ERROR: 'INTERNAL_ERROR',
} as const;

/**
 * Type guards for message types
 */
export function isAuthMessage(msg: unknown): msg is AuthMessage {
  return (
    typeof msg === 'object' &&
    msg !== null &&
    'op' in msg &&
    msg.op === 'auth'
  );
}

export function isSubscribeMessage(msg: unknown): msg is SubscribeMessage {
  return (
    typeof msg === 'object' &&
    msg !== null &&
    'op' in msg &&
    msg.op === 'subscribe'
  );
}

export function isUnsubscribeMessage(msg: unknown): msg is UnsubscribeMessage {
  return (
    typeof msg === 'object' &&
    msg !== null &&
    'op' in msg &&
    msg.op === 'unsubscribe'
  );
}

export function isPingMessage(msg: unknown): msg is PingMessage {
  return (
    typeof msg === 'object' &&
    msg !== null &&
    'op' in msg &&
    msg.op === 'ping'
  );
}

/**
 * Helper to get message priority
 */
export function getMessagePriority(msg: ServerMessage): MessagePriority {
  if ('op' in msg) {
    return MessagePriority.CRITICAL;
  }

  if ('type' in msg) {
    switch (msg.type) {
      case 'snapshot':
      case 'error':
        return MessagePriority.CRITICAL;
      case 'update':
      case 'order':
      case 'balance':
        return MessagePriority.HIGH;
      case 'trade':
        return MessagePriority.NORMAL;
      case 'ticker':
      case 'candle':
        return MessagePriority.LOW;
      default:
        return MessagePriority.NORMAL;
    }
  }

  return MessagePriority.NORMAL;
}

/**
 * Build channel key for subscription tracking
 */
export function buildChannelKey(config: ChannelConfig): string {
  const parts = [config.name];
  if (config.market) {
    parts.push(config.market);
  }
  if (config.interval) {
    parts.push(config.interval);
  }
  return parts.join(':');
}

/**
 * Parse channel key back to config
 */
export function parseChannelKey(key: string): ChannelConfig {
  const [name, market, interval] = key.split(':');
  return { name, market, interval };
}
