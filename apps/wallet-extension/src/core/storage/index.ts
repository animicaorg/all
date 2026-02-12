// Chrome storage wrapper with type safety

import type { EncryptedVault, VaultData, StorageState } from '../../types/vault';

const STORAGE_KEY_VAULT = 'encrypted_vault';
const STORAGE_KEY_STATE = 'storage_state';
const STORAGE_KEY_ACTIVE_WALLET_ID = 'active_wallet_id';

export async function saveVault(vault: EncryptedVault): Promise<void> {
  await chrome.storage.local.set({
    [STORAGE_KEY_VAULT]: vault,
  });
}

export async function loadVault(): Promise<EncryptedVault | null> {
  const result = await chrome.storage.local.get(STORAGE_KEY_VAULT);
  return result[STORAGE_KEY_VAULT] || null;
}

export async function saveState(state: StorageState): Promise<void> {
  await chrome.storage.local.set({
    [STORAGE_KEY_STATE]: state,
  });
}

export async function loadState(): Promise<StorageState> {
  const result = await chrome.storage.local.get(STORAGE_KEY_STATE);
  return result[STORAGE_KEY_STATE] || {
    isLocked: true,
  };
}

export async function clearAll(): Promise<void> {
  await chrome.storage.local.clear();
}

export async function saveActiveWalletId(walletId: string): Promise<void> {
  await chrome.storage.local.set({
    [STORAGE_KEY_ACTIVE_WALLET_ID]: walletId,
  });
}

export async function loadActiveWalletId(): Promise<string | null> {
  const result = await chrome.storage.local.get(STORAGE_KEY_ACTIVE_WALLET_ID);
  const walletId = result[STORAGE_KEY_ACTIVE_WALLET_ID];
  return typeof walletId === 'string' && walletId.length > 0 ? walletId : null;
}

// Session storage for temporary unlocked data
let unlockedVaultData: VaultData | null = null;
let unlockTimer: NodeJS.Timeout | null = null;

export function setUnlockedVault(data: VaultData, autoLockMinutes: number): void {
  unlockedVaultData = data;
  
  if (unlockTimer) {
    clearTimeout(unlockTimer);
  }
  
  if (autoLockMinutes > 0) {
    unlockTimer = setTimeout(() => {
      lockVault();
    }, autoLockMinutes * 60 * 1000);
  }
}

export function getUnlockedVault(): VaultData | null {
  return unlockedVaultData;
}

export function lockVault(): void {
  unlockedVaultData = null;
  if (unlockTimer) {
    clearTimeout(unlockTimer);
    unlockTimer = null;
  }
}

export function isVaultUnlocked(): boolean {
  return unlockedVaultData !== null;
}
