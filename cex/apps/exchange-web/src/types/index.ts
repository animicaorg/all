export interface Market {
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  lastPrice: number;
  change24h: number;
  volume24h: number;
  high24h: number;
  low24h: number;
  priceTick?: number;
  sizeStep?: number;
  minOrderSize?: number;
  makerFeeBps?: number;
  takerFeeBps?: number;
}

export interface OrderbookEntry {
  price: number;
  quantity: number;
  total: number;
}

export interface Orderbook {
  symbol: string;
  bids: OrderbookEntry[];
  asks: OrderbookEntry[];
  timestamp: number;
}

export interface Trade {
  id: string;
  symbol: string;
  price: number;
  quantity: number;
  side: 'buy' | 'sell';
  timestamp: number;
}

export interface Order {
  id: string;
  clientOrderId: string;
  symbol: string;
  side: 'buy' | 'sell';
  type: 'limit' | 'market';
  price?: number;
  quantity: number;
  filledQuantity: number;
  status: 'pending' | 'open' | 'filled' | 'cancelled' | 'rejected';
  createdAt: number;
  updatedAt: number;
}

export interface Balance {
  asset: string;
  available: number;
  locked: number;
  total: number;
}

export interface UserTrade {
  id: string;
  orderId: string;
  symbol: string;
  side: 'buy' | 'sell';
  price: number;
  quantity: number;
  fee: number;
  feeAsset: string;
  timestamp: number;
}

export interface CreateOrderRequest {
  symbol: string;
  side: 'buy' | 'sell';
  type: 'limit' | 'market';
  price?: number;
  quantity: number;
  clientOrderId?: string;
  idempotencyKey?: string;
}

export interface WSMessage {
  channel: string;
  symbol?: string;
  data: unknown;
}

export interface PlatformStats {
  volume24h: number;
  activeTraders: number;
  uptimePercentage: number | null;
}
