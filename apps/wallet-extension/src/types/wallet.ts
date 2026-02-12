// Wallet and account types

export interface WalletEntry {
  label: string;
  address: string;
  alg_id: number;
  alg_name?: string;
  public_key_hex: string;
  created_at: string;
  pub_fingerprint?: string;
  secret_key_hex?: string;
  private_key_enc?: string;
  keystore?: Record<string, unknown>;
  meta?: Record<string, unknown>;
}

export interface WalletsJson {
  format: 'animica.wallets';
  version: 2;
  created_at: string;
  updated_at: string;
  default?: string | null;
  wallets: WalletEntry[];
}

export interface Account {
  label: string;
  address: string;
  algId: number;
  algName: string;
  publicKey: Uint8Array;
  secretKey?: Uint8Array;
  createdAt: string;
  watchOnly?: boolean;
}

export interface AddressRecord {
  hrp: string;
  algId: number;
  digest: Uint8Array;
}

export interface BalanceInfo {
  confirmed: bigint;
  pendingOutgoing: bigint;
  available: bigint;
}
