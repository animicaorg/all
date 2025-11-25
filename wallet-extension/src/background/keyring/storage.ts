/**
 * Keyring storage helpers.
 *
 * - Secrets (encrypted vault envelope) are stored ONLY in chrome.storage.local.
 * - Non-secret public state (addresses, labels, active index) is also kept in local;
 *   you may mirror to chrome.storage.sync elsewhere if you add an explicit opt-in,
 *   but do NOT put secret material in sync.
 *
 * The module provides a small, promise-based wrapper over MV3 storage with a
 * test-friendly in-memory fallback (used by unit tests or non-extension envs).
 */

import type { VaultEnvelope } from './vault';

// ------------------------------ Keys & types ------------------------------

const VAULT_KEY = 'animica:keyring:vault';
const PUBSTATE_KEY = 'animica:keyring:public';
const STATE_KEY = 'animica:keyring:state';
const SESSION_KEY = 'animica:keyring:session';

export type KeyAlgo = 'dilithium3' | 'sphincs_shake_128s';

export interface PublicAccount {
  /** bech32m address (e.g., anim1...) */
  address: string;
  /** PQ signature algorithm used for this account */
  algo: KeyAlgo;
  /** optional user label */
  label?: string;
}

export interface PublicState {
  version: 1;
  accounts: PublicAccount[];
  activeIndex: number; // index into accounts (>=0) or -1 if none
  createdAt: number;
  updatedAt: number;
}

// ------------------------------ Storage wrapper ------------------------------

type AnyObject = Record<string, any>;

function getMemStore(scope: 'local' | 'session'): AnyObject {
  const root = (globalThis as any).__animica_mem_store || ((globalThis as any).__animica_mem_store = {});
  return (root[scope] ||= {});
}

async function storageLocalGet<T>(key: string): Promise<T | undefined> {
  if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
    const obj = await chrome.storage.local.get(key);
    return obj?.[key] as T | undefined;
  }
  return getMemStore('local')[key] as T | undefined;
}

async function storageLocalSet<T>(key: string, value: T): Promise<void> {
  if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
    await chrome.storage.local.set({ [key]: value });
    return;
  }
  getMemStore('local')[key] = value;
}

async function storageLocalRemove(key: string): Promise<void> {
  if (typeof chrome !== 'undefined' && chrome?.storage?.local) {
    await chrome.storage.local.remove(key);
    return;
  }
  delete getMemStore('local')[key];
}

// ------------------------------ Vault envelope (secret) ------------------------------

/** Save encrypted vault envelope (AES-GCM payload). */
export async function saveVaultEnvelope(envelope: VaultEnvelope): Promise<void> {
  await storageLocalSet(VAULT_KEY, envelope);
}

/** Load encrypted vault envelope; returns null if missing. */
export async function loadVaultEnvelope(): Promise<VaultEnvelope | null> {
  const v = await storageLocalGet<VaultEnvelope>(VAULT_KEY);
  return v ?? null;
}

/** Delete the stored vault envelope (e.g., on reset). */
export async function clearVaultEnvelope(): Promise<void> {
  await storageLocalRemove(VAULT_KEY);
}

/** Quick existence check (without fetching). */
export async function hasVaultEnvelope(): Promise<boolean> {
  return (await loadVaultEnvelope()) !== null;
}

// Back-compat helpers used by the keyring facade and tests
export async function saveVault<T>(state: T): Promise<void> {
  await storageLocalSet(STATE_KEY, state as any);
}

export async function loadVault<T = any>(): Promise<T | null> {
  const v = await storageLocalGet<T>(STATE_KEY);
  return (v as any) ?? null;
}

export async function clearVault(): Promise<void> {
  await storageLocalRemove(STATE_KEY);
}

export async function saveSession<T>(session: T): Promise<void> {
  await storageLocalSet(SESSION_KEY, session as any);
}

export async function loadSession<T = any>(): Promise<T | null> {
  const s = await storageLocalGet<T>(SESSION_KEY);
  return (s as any) ?? null;
}

export async function clearSession(): Promise<void> {
  await storageLocalRemove(SESSION_KEY);
}

// ------------------------------ Public (non-secret) state ------------------------------

/** Initialize a fresh PublicState from a set of accounts (activeIndex defaults to 0 or -1). */
export function makePublicState(accounts: PublicAccount[], activeIndex?: number): PublicState {
  const now = Date.now();
  const idx = typeof activeIndex === 'number' ? activeIndex : (accounts.length > 0 ? 0 : -1);
  return {
    version: 1,
    accounts: [...accounts],
    activeIndex: idx,
    createdAt: now,
    updatedAt: now,
  };
}

/** Persist public state. */
export async function savePublicState(state: PublicState): Promise<void> {
  const updated = { ...state, updatedAt: Date.now() } as PublicState;
  await storageLocalSet(PUBSTATE_KEY, updated);
}

/** Load public state or null if none exists. */
export async function loadPublicState(): Promise<PublicState | null> {
  const s = await storageLocalGet<PublicState>(PUBSTATE_KEY);
  if (!s || s.version !== 1) return null;
  // Basic shape validation
  if (!Array.isArray(s.accounts) || typeof s.activeIndex !== 'number') return null;
  return s;
}

/** Clear public state. */
export async function clearPublicState(): Promise<void> {
  await storageLocalRemove(PUBSTATE_KEY);
}

/** Convenience: set currently active account index (no bounds change to accounts). */
export async function setActiveAccountIndex(index: number): Promise<void> {
  const cur = (await loadPublicState()) ?? makePublicState([], -1);
  const next: PublicState = { ...cur, activeIndex: index, updatedAt: Date.now() };
  await savePublicState(next);
}

/** Add or replace accounts list (keeps active index in range if possible). */
export async function setAccounts(accounts: PublicAccount[]): Promise<void> {
  const cur = (await loadPublicState()) ?? makePublicState([], -1);
  let active = cur.activeIndex;
  if (accounts.length === 0) active = -1;
  else if (active < 0 || active >= accounts.length) active = 0;
  const next: PublicState = { ...cur, accounts: [...accounts], activeIndex: active, updatedAt: Date.now() };
  await savePublicState(next);
}

/** True if there is at least one public account stored. */
export async function hasAnyAccount(): Promise<boolean> {
  const s = await loadPublicState();
  return !!(s && s.accounts && s.accounts.length > 0);
}

// ------------------------------ Full reset ------------------------------

/** Wipe all keyring-related storage (both secret and public parts). */
export async function clearAllKeyringStorage(): Promise<void> {
  await clearVaultEnvelope();
  await clearPublicState();
}
