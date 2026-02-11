// Wallet and account types

export interface WalletEntry {
  label: string;
  address: string;
  alg_id: number;
  alg_name: string;
  public_key_hex: string;
  secret_key_hex: string;
  created_at: string;
}

export interface WalletsJson {
  version: number;
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
  version: number;
  algId: number;
  digest: Uint8Array;
}

export interface BalanceInfo {
  confirmed: bigint;
  pendingOutgoing: bigint;
  available: bigint;
}
