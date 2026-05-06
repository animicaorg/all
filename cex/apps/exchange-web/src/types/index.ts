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

export interface Candle {
  timestamp: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Order {
  id: string;
  clientOrderId: string;
  symbol: string;
  side: 'buy' | 'sell';
  type: 'limit' | 'market' | 'post_only';
  price?: number;
  quantity: number;
  filledQuantity: number;
  status: 'pending' | 'open' | 'filled' | 'cancelled' | 'rejected' | 'expired';
  createdAt: number;
  updatedAt: number;
}

export interface Balance {
  asset: string;
  available: number;
  locked: number;
  total: number;
}

export interface AssetNetwork {
  assetNetworkId: string;
  code: string;
  name: string;
  type: string;
  provider: string;
  bitgoCoin?: string | null;
  rpcUrl?: string | null;
  depositsEnabled: boolean;
  withdrawalsEnabled: boolean;
  minWithdrawalAtoms: string;
  withdrawalFeeAtoms: string;
  flatFee: boolean;
}

export interface Asset {
  symbol: string;
  name: string;
  decimals: number;
  isEnabled: boolean;
  networks: AssetNetwork[];
}

export interface DepositAddress {
  id: string;
  assetNetworkId: string;
  symbol: string;
  networkCode: string;
  address: string;
  tag?: string | null;
  label?: string | null;
  assignedAt: number;
  created?: boolean;
}

export interface Deposit {
  id: string;
  status: string;
  assetNetworkId?: string;
  amount: string;
  txid: string;
  address: string;
  tag?: string | null;
  confirmations: number;
  confirmationsRequired: number;
  detectedAt?: string;
  confirmedAt?: string | null;
  creditedAt?: string | null;
  blockHeight?: number | null;
  blockHash?: string | null;
  networkCode?: string;
  symbol?: string;
}

export interface Withdrawal {
  id: string;
  status: string;
  assetNetworkId?: string;
  amount: string;
  feeAmount: string;
  totalDebitAmount: string;
  destinationAddress: string;
  destinationTag?: string | null;
  txid?: string | null;
  riskScore?: number;
  riskFlags?: string[];
  requestedAt?: string;
  createdAt?: string;
}

export interface CreateWithdrawalRequest {
  assetNetworkId: string;
  destinationAddress: string;
  destinationTag?: string;
  amountAtoms: string;
  clientWithdrawalId?: string;
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
  type: 'limit' | 'market' | 'post_only';
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
