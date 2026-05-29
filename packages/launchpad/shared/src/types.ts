import type {
  ActivityType,
  ProjectStatus,
  RiskLevel,
  WalletType
} from "./constants";

export type ID = string;

export interface AnimicaNetworkInfo {
  chainId: number;
  chainName: string;
  rpcUrl?: string;
  explorerUrl?: string;
  height?: number;
}

export interface AnimicaTx {
  txid: string;
  from?: string;
  to?: string;
  amount?: string;
  blockHeight?: number;
  status?: "pending" | "confirmed" | "failed";
  raw?: unknown;
}

export interface WalletAccountInfo {
  address: string;
  type: WalletType;
  chainId?: number;
  balanceAnm?: string;
}

export interface SessionUser {
  id: string;
  primaryWalletAddress: string;
  primaryWalletType: WalletType;
  role: "USER" | "ADMIN" | "MODERATOR";
  username?: string | null;
  avatarUrl?: string | null;
}

export interface ProjectCardData {
  id: string;
  slug: string;
  name: string;
  symbol: string;
  description: string;
  logoUrl?: string | null;
  bannerUrl?: string | null;
  category?: string | null;
  creatorWallet: string;
  status: ProjectStatus;
  verified: boolean;
  featured: boolean;
  riskLevel: RiskLevel;
  marketCap?: string | null;
  liquidityAnm?: string | null;
  volume24hAnm?: string | null;
  holderCount?: number | null;
  commentCount: number;
  followCount: number;
  createdAt: string;
}

export interface ActivityItem {
  id: string;
  type: ActivityType;
  projectSlug?: string | null;
  projectName?: string | null;
  projectSymbol?: string | null;
  walletAddress?: string | null;
  amountInAnm?: string | null;
  amountOut?: string | null;
  txHash?: string | null;
  createdAt: string;
}

export interface QuoteRequest {
  projectSlug: string;
  side: "BUY" | "SELL";
  amountInAnm?: string;
  amountInToken?: string;
  slippageBps?: number;
}

export interface Quote {
  side: "BUY" | "SELL";
  inputAmount: string;
  outputAmount: string;
  priceAnm: string;
  feeAnm: string;
  minOutput: string;
  routeLabel: string;
  expiresAt: string;
}

export interface TransactionRequest {
  method: string;
  params: Record<string, unknown>;
  description: string;
}

export interface MarketCandle {
  t: number;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface MarketData {
  candles: MarketCandle[];
  priceAnm: string;
  priceChange24h: number;
  liquidityAnm: string;
  volume24hAnm: string;
  marketCapAnm: string;
}
