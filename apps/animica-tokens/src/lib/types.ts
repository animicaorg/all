export type TokenRecord = {
  id: string;
  address: string;
  name: string;
  symbol: string;
  decimals: number;
  description: string;
  metadataUri: string;
  imageUri: string;
  website?: string;
  twitter?: string;
  telegram?: string;
  discord?: string;
  github?: string;
  creator: string;
  createdAt: string;
  hidden?: boolean;
  totalSupply?: string;
  maxSupply?: string;
  mintable?: boolean;
  volume24h?: string;
  liquidity?: string;
  swaps24h?: number;
};

export type PoolRecord = {
  id: string;
  pairAddress: string;
  tokenA: string;
  tokenB: string;
  tokenAAddress: string;
  tokenBAddress: string;
  feeBps: number;
  reserveA: string;
  reserveB: string;
  lpSupply: string;
  createdAt: string;
  creator: string;
  metadataUri?: string;
};

export type SwapRecord = {
  id: string;
  pairId: string;
  pairAddress: string;
  tokenIn: string;
  tokenOut: string;
  amountIn: string;
  amountOut: string;
  trader: string;
  txHash?: string;
  createdAt: string;
};

export type LiquidityRecord = {
  id: string;
  pairId: string;
  pairAddress: string;
  provider: string;
  kind: "add" | "remove";
  amountA: string;
  amountB: string;
  lpAmount: string;
  txHash?: string;
  createdAt: string;
};

export type ReportRecord = {
  id: string;
  tokenId: string;
  reason: string;
  reporter: string;
  createdAt: string;
  resolved: boolean;
};

export type QuoteResponse = {
  ok: boolean;
  amountIn?: string;
  amountOut?: string;
  priceImpactBps?: number;
  feeBps?: number;
  error?: string;
};

export type PortfolioResponse = {
  address: string;
  createdTokens: TokenRecord[];
  lpPositions: Array<{
    pairId: string;
    pairAddress: string;
    lpAmount: string;
    tokenA: string;
    tokenB: string;
    shareBps: number;
  }>;
  recentActivity: Array<SwapRecord | LiquidityRecord>;
};

export type StatsResponse = {
  tokenCount: number;
  poolCount: number;
  swapCount24h: number;
  liquidityNotional: string;
};
