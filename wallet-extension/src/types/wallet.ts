/**
 * Animica Wallet — Core Models
 * ------------------------------------------------------------
 * Canonical TypeScript types for accounts, networks, and dapp permissions.
 * These are shared across background, UI, and the in-page provider.
 */

/* --------------------------------- Accounts --------------------------------- */

export type PQAlgo = 'dilithium3' | 'sphincs+';

/** Bech32m Animica address string (e.g. anim1...) */
export type Address = string;

/** One wallet account derived from the mnemonic. */
export interface Account {
  /** Bech32m address */
  address: Address;
  /** Post-quantum signature algorithm used by this account */
  algo: PQAlgo;
  /** Derivation index within the keyring (0-based) */
  index: number;
  /** Optional human label set by the user */
  label?: string;
  /** Optional hex public key (algorithm-specific); for display/debug only */
  publicKeyHex?: string;
  /** Unix epoch (ms) when this account was created/imported */
  createdAt: number;
}

/** Keyring lock state (persisted in storage) */
export type KeyringState = 'locked' | 'unlocked';

/* --------------------------------- Networks --------------------------------- */

export type KnownNetworkId =
  | 'animica-mainnet'
  | 'animica-testnet'
  | 'animica-devnet';

/** Feature flags used by UI to enable/disable panels. */
export interface NetworkFeatures {
  aicf?: boolean;       // AI/Quantum compute capabilities
  randomness?: boolean; // Randomness beacon & tools
  zk?: boolean;         // zk-verify system enabled
  da?: boolean;         // Data-availability helper endpoints
}

/** Chain currency info for formatting amounts. */
export interface Currency {
  symbol: string;   // e.g. "ANM"
  decimals: number; // e.g. 18
}

/** Full network config known to the wallet. */
export interface Network {
  /** Stable identifier used in storage and selection */
  id: KnownNetworkId | string;
  /** EVM-like numeric chain id (also stored on-chain) */
  chainId: number;
  /** Human name shown in UI */
  name: string;
  /** HTTP(S) JSON-RPC endpoint */
  rpcUrl: string;
  /** Optional WS endpoint for subscriptions (newHeads, pendingTxs) */
  wsUrl?: string;
  /** Optional explorer base URL for deep links */
  explorerUrl?: string;
  /** Currency metadata */
  currency: Currency;
  /** Optional per-network feature flags */
  features?: NetworkFeatures;
  /** Optional block time hint (ms) for UX estimates */
  avgBlockTimeMs?: number;
}

/* ---------------------------- Permissions & Sessions ---------------------------- */

export type PermissionScope = 'connect' | 'sign' | 'call';

/** Snapshot of what a given origin is allowed to do. */
export interface OriginPermission {
  /** Origin hostname (scheme+host+port), e.g. https://app.example */
  origin: string;
  /** Granted scopes */
  scopes: PermissionScope[];
  /** Whether access is currently granted (revoked if false) */
  granted: boolean;
  /** Timestamps (ms) for audit/history */
  createdAt: number;
  updatedAt: number;
  lastUsedAt?: number;
}

/** A live connection from a site to the wallet (used for events fanout). */
export interface Session {
  origin: string;
  /** The account address this session is currently using */
  account: Address;
  /** The chain this session is bound to */
  chainId: number;
  /** Browser tab id if available (routing convenience) */
  tabId?: number;
  /** Connected timestamp (ms) */
  connectedAt: number;
}

/* ------------------------------ Wallet Snapshot ------------------------------ */

/** Minimal snapshot shape used by popup/UI to render state quickly. */
export interface WalletSnapshot {
  accounts: Account[];
  activeAccount?: Address;
  network: Network;
  head?: { number: number; hash: string };
  state: KeyringState;
}

/* --------------------------------- Storage ---------------------------------- */

/**
 * Persisted storage root (background/migrations.ts owns versioning).
 * Only add optional fields in-place; breaking changes must bump `version`
 * and add a migration step.
 */
export interface WalletStorage {
  version: number;
  keyringState: KeyringState;
  accounts: Account[];
  activeAccount?: Address;

  /** Networks known to the wallet and the selected one */
  networks: Record<string, Network>;
  activeNetworkId: string;

  /** Dapp permissions keyed by origin */
  permissions: Record<string, OriginPermission>;
  /** Active sessions (cleared on browser restart or idle timeout) */
  sessions: Session[];

  /** Misc UX flags (first run, dismissed banners, etc.) */
  ux?: Record<string, unknown>;
}

/* --------------------------------- Helpers ---------------------------------- */

/** Utility: ensure a scope set contains at least "connect". */
export function normalizeScopes(
  scopes?: PermissionScope[]
): PermissionScope[] {
  const base: PermissionScope[] = ['connect'];
  const extra = Array.isArray(scopes) ? scopes : [];
  return Array.from(new Set([...base, ...extra]));
}
