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
  type: 'limit' | 'market' | 'post_only' | 'LIMIT' | 'MARKET' | 'POST_ONLY';
  price?: number;
  quantity: number;
  clientOrderId?: string;
  idempotencyKey?: string;
}

export interface UsdQuote {
  asset: string;
  usd: number;
  source: 'google-finance' | 'derived';
  sourceUrl?: string;
  derivedFrom?: string;
  fetchedAt: string;
}

export interface AirdropStatus {
  settings: {
    asset: string;
    claimAmount: string;
    claimAmountAtoms: string;
    cooldownSeconds: number;
    enabled: boolean;
  };
  poolBalance: string;
  poolBalanceAtoms: string;
  claimable: boolean;
  lastClaimAt: string | null;
  nextClaimAt: string | null;
}

export interface AirdropTransfer {
  id: string;
  asset: string;
  amount: string;
  amountAtoms: string;
  claimedAt?: string;
  depositedAt?: string;
}

export interface ReferralHistoryItem {
  id: string;
  status: string;
  reason: string | null;
  referredEmail: string | null;
  rewardAtoms: string;
  reward: string;
  createdAt: string;
  updatedAt?: string;
  rewardedAt: string | null;
}

export interface ReferralSummary {
  code: string;
  referralLink: string;
  reward: {
    asset: string;
    amount: string;
    amountAtoms: string;
    signupAmount: string;
    signupAmountAtoms: string;
    source: string;
  };
  totals: {
    referrals: number;
    qualified: number;
    rewarded: number;
    earnedAtoms: string;
    earned: string;
  };
  recent: ReferralHistoryItem[];
}

export interface ApiKeySummary {
  id: string;
  name: string;
  keyPrefix: string;
  scopes: string[];
  createdAt: string | null;
  lastUsedAt: string | null;
  revokedAt: string | null;
}

export interface CreatedApiKey {
  apiKey: ApiKeySummary;
  secret: string;
}

export type TradingBotMode = 'DCA' | 'GRID' | 'MAKER';

export interface TradingBot {
  id: string;
  mode: TradingBotMode;
  market: string;
  status: 'RUNNING' | 'STOPPED' | 'ERROR';
  config: {
    side?: 'buy' | 'sell';
    quantity?: number;
    intervalSeconds?: number;
    spacingPct?: number;
    spreadPct?: number;
    levels?: number;
  };
  lastError: string | null;
  lastRunAt: string | null;
  nextRunAt: string | null;
  createdAt: string | null;
  updatedAt: string | null;
}

export interface StartTradingBotRequest {
  mode: TradingBotMode;
  market: string;
  side?: 'buy' | 'sell';
  quantity: number;
  intervalSeconds?: number;
  spacingPct?: number;
  spreadPct?: number;
  levels?: number;
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
