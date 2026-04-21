// Vault and storage types

export interface VaultData {
  accounts: Account[];
  permissions: Record<string, DappPermission>;
  networkConfigs: Record<string, NetworkConfig>;
  currentNetwork: string;
  currentAccount?: string;
  txCache: Record<string, PendingTx>;
  watchedTokens?: WatchedToken[];
  settings: VaultSettings;
}

export interface VaultSettings {
  autoLockMinutes: number;
  showTestNetworks: boolean;
  defaultGasPrice: number;
  defaultGasLimit: number;
}

export interface EncryptedVault {
  version: number;
  salt: string;
  iv: string;
  ciphertext: string;
}

export interface StorageState {
  vault?: EncryptedVault;
  isLocked: boolean;
  lastUnlockAt?: number;
}

import { Account } from './wallet';
import { DappPermission } from './provider';
import { NetworkConfig } from './network';
import { PendingTx } from './tx';
import { WatchedToken } from './token';
