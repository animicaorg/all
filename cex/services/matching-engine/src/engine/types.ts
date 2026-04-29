/**
 * Core types for the matching engine
 * All prices and sizes use BigInt "atoms" to avoid floating point issues
 */

export type OrderSide = "BUY" | "SELL";
export type OrderType = "LIMIT" | "MARKET";
export type TimeInForce = "GTC" | "IOC" | "FOK" | "POST_ONLY";
export type OrderStatus =
  | "NEW"
  | "ACCEPTED"
  | "PARTIAL_FILL"
  | "FILLED"
  | "CANCELED"
  | "REJECTED"
  | "EXPIRED"
  | "CANCELED_REPLACED";

/**
 * Market configuration
 */
export interface MarketConfig {
  id: string;
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  priceTick: bigint; // minimum price increment in atoms
  sizeStep: bigint; // minimum size increment in atoms
  minOrderSize: bigint; // minimum order size in atoms
  makerFeeBps: number; // basis points (1 bp = 0.01%)
  takerFeeBps: number;
  feeAsset: string;
  active: boolean;
}

/**
 * Order representation in memory
 */
export interface Order {
  id: string;
  userId: string;
  clientOrderId: string;
  marketId: string;
  side: OrderSide;
  orderType: OrderType;
  timeInForce: TimeInForce;
  priceAtoms: bigint; // 0 for market orders
  sizeAtoms: bigint;
  filledAtoms: bigint;
  remainingAtoms: bigint;
  postOnly: boolean;
  status: OrderStatus;
  acceptedAt: Date;
  replaceOf?: string;
}

/**
 * Trade result
 */
export interface Trade {
  id: string;
  marketId: string;
  makerOrderId: string;
  takerOrderId: string;
  priceAtoms: bigint;
  sizeAtoms: bigint;
  quoteAmountAtoms: bigint;
  makerFeeAtoms: bigint;
  takerFeeAtoms: bigint;
  feeAsset: string;
  feeBpsMaker: number;
  feeBpsTaker: number;
  sequence: bigint;
  createdAt: Date;
}

/**
 * Fill result when matching
 */
export interface Fill {
  makerOrderId: string;
  takerOrderId: string;
  priceAtoms: bigint;
  sizeAtoms: bigint;
  makerFeeAtoms: bigint;
  takerFeeAtoms: bigint;
}

/**
 * Order event for audit trail
 */
export interface OrderEvent {
  id: string;
  orderId: string;
  marketId: string;
  eventType: string;
  sequence: bigint;
  payload: Record<string, any>;
  createdAt: Date;
}

/**
 * Outbox event for publishing
 */
export interface OutboxEvent {
  id: string;
  marketId: string;
  seq: bigint;
  type: "ORDER_EVENT" | "TRADE_EVENT";
  key: string;
  payload: Record<string, any>;
  createdAt: Date;
  publishedAt?: Date;
}

/**
 * Command to place a limit order
 */
export interface PlaceLimitOrderCommand {
  userId: string;
  clientOrderId: string;
  marketId: string;
  side: OrderSide;
  priceAtoms: bigint;
  sizeAtoms: bigint;
  timeInForce: TimeInForce;
  postOnly: boolean;
  idempotencyKey: string;
}

/**
 * Command to place a market order
 */
export interface PlaceMarketOrderCommand {
  userId: string;
  clientOrderId: string;
  marketId: string;
  side: OrderSide;
  sizeAtoms: bigint;
  idempotencyKey: string;
}

/**
 * Command to cancel an order
 */
export interface CancelOrderCommand {
  userId: string;
  orderId: string;
  idempotencyKey: string;
}

/**
 * Command to replace an order
 */
export interface ReplaceOrderCommand {
  userId: string;
  orderId: string;
  newPriceAtoms?: bigint;
  newSizeAtoms?: bigint;
  timeInForce?: TimeInForce;
  postOnly?: boolean;
  idempotencyKey: string;
}

/**
 * Result of order processing
 */
export interface OrderResult {
  success: boolean;
  order?: any;
  fills: Fill[];
  trades: Trade[];
  events: OrderEvent[];
  rejectReason?: string;
}

/**
 * Price level in the orderbook
 */
export interface PriceLevel {
  priceAtoms: bigint;
  orders: any[];
  totalSize: bigint;
}
