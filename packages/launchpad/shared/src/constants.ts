export const BRAND = {
  name: "Animica Launch",
  shortName: "Animica.xyz",
  domain: "animica.xyz",
  description:
    "Discover, launch, and follow new Animica ecosystem projects from one Animica-native launchpad.",
  tagline: "Launch on Animica.",
  coin: "ANM",
  coinName: "Animica",
  decimals: 9
} as const;

export const URLS = {
  app: "https://animica.xyz",
  main: "https://animica.org",
  wallet: "https://wallet.animica.org",
  explorer: "https://explorer.animica.org",
  trade: "https://trade.animica.org",
  buy: "https://buy.animica.org",
  discord: "https://discord.gg/vQHJc2jWUJ",
  extensionInstall: "https://animica.org/wallet"
} as const;

export const NETWORK = {
  defaultChainId: 1,
  defaultRpcUrl: "https://rpc.animica.org",
  chainName: "Animica Mainnet"
} as const;

export const RATE_LIMITS = {
  launchPerHour: 5,
  commentsPerMinute: 8,
  reportsPerHour: 10,
  noncesPerMinute: 12
} as const;

// Trading fee charged in ANM on every buy/sell on Animica Launch.
// Configurable per-env via NEXT_PUBLIC_TRADE_FEE_BPS; defaults to 0.
export const TRADE_FEE_BPS_DEFAULT = 0;

// Bonding-curve defaults (linear): p(x) = p0 + k·x, where x is tokens sold.
// All values in ANM. Override with env to retarget.
export const BONDING_DEFAULTS = {
  startPriceAnm: 0.000001,
  saleSupply: 30_000_000,
  // Cumulative ANM raised by the time saleSupply is exhausted.
  targetRaiseAnm: 250_000
} as const;

export const RISK_LEVELS = ["UNKNOWN", "LOW", "MEDIUM", "HIGH"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export const PROJECT_STATUSES = [
  "DRAFT",
  "PENDING",
  "LIVE",
  "HIDDEN",
  "REJECTED"
] as const;
export type ProjectStatus = (typeof PROJECT_STATUSES)[number];

export const ACTIVITY_TYPES = [
  "PROJECT_CREATED",
  "PROJECT_APPROVED",
  "PROJECT_FEATURED",
  "COMMENT_CREATED",
  "BUY",
  "SELL",
  "LIQUIDITY_ADDED",
  "VERIFIED",
  "RISK_UPDATED",
  "FOLLOWED"
] as const;
export type ActivityType = (typeof ACTIVITY_TYPES)[number];

export const WALLET_TYPES = ["ANIMICA_EXTENSION", "ANIMICA_WEB", "MOCK"] as const;
export type WalletType = (typeof WALLET_TYPES)[number];

export const CATEGORIES = [
  { slug: "ai", name: "AI & Agents" },
  { slug: "infra", name: "Infrastructure" },
  { slug: "defi", name: "DeFi" },
  { slug: "social", name: "Social" },
  { slug: "gaming", name: "Gaming" },
  { slug: "tools", name: "Dev Tools" },
  { slug: "media", name: "Media" },
  { slug: "experimental", name: "Experimental" }
] as const;

export const DISCLAIMER =
  "Animica Launch is an open Animica ecosystem discovery platform. New projects are experimental and risky. Nothing here is financial advice.";

export const SIGN_MESSAGE_TEMPLATE = (address: string, nonce: string, expiresAtIso: string) =>
  `Sign in to animica.xyz with address ${address}. Nonce: ${nonce}. Expires: ${expiresAtIso}.`;
