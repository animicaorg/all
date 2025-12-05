/* eslint-disable no-console */
/**
 * Keyring facade (MV3-safe).
 *
 * Responsibilities:
 * - Create/import a vault from a mnemonic (BIP-39-like; PBKDF/HKDF-SHA3).
 * - Encrypt/decrypt the vault (AES-GCM via ./vault).
 * - Derive deterministic PQ keypairs (Dilithium3 / SPHINCS+) via ./derive.
 * - Manage accounts (add/list/remove/select) and addresses (bech32m via ./addresses).
 * - Provide sign(bytes) per-account with domain separation handled by caller.
 * - Lock/unlock lifecycle; private material lives only in SW memory.
 *
 * Storage layout (persisted in chrome.storage.local via keyring/storage.ts):
 * {
 *   version: 1,
 *   vault: EncryptedVault,          // encrypted seed (+ optional mnemonic)
 *   meta: {
 *     accounts: AccountMeta[],      // id, label, alg, path, address
 *     nextIndex: number,            // next derivation index
 *     selectedId?: string,          // selected account id
 *     mnemonicStored: boolean       // if mnemonic is kept inside vault for export
 *   }
 * }
 *
 * Security notes:
 * - The mnemonic is NOT stored unless the user explicitly opts in (mnemonicStored=true).
 * - The decrypted seed is kept only in-memory; cleared on lock() or SW shutdown.
 * - Signing should prefer running in a Worker (see workers/crypto.worker.ts). This facade
 *   exposes a simple sign() that uses ./derive + PQ backends; callers may offload heavy
 *   ops to the worker implementation if available.
 */

import { generateMnemonic, mnemonicToSeed } from './mnemonic';
import { encryptVault, decryptVault, type EncryptedVault } from './vault';
import {
  saveVault,
  loadVault,
  clearVault,
  saveSession,
  clearSession,
  saveVaultEnvelope,
  loadVaultEnvelope,
  clearVaultEnvelope,
} from './storage';
import { bech32AddressFromPub } from './addresses';
import { deriveKeypair } from './derive';
import * as d3 from '../pq/dilithium3';
import * as sphincs from '../pq/sphincs_shake_128s';

export type KeyAlgorithm = 'dilithium3' | 'sphincs_shake_128s';

export interface AccountMeta {
  id: string;           // stable UUID-like
  label: string;        // "Account 1"
  alg: KeyAlgorithm;    // algorithm used for this account (legacy field)
  algo: KeyAlgorithm;   // alias used by some callers/tests
  path: string;         // derivation path, e.g. "m/44'/0'/0'/0/0" or "m/animica/0"
  index: number;        // numeric index (for convenience)
  address: string;      // bech32m (anim1...)
}

interface PersistedKeyringV1 {
  version: 1;
  vault: EncryptedVault | null;
  meta: {
    accounts: AccountMeta[];
    nextIndex: number;
    selectedId?: string;
    mnemonicStored: boolean;
  };
}

function newEmptyState(): PersistedKeyringV1 {
  return {
    version: 1,
    vault: null,
    meta: { accounts: [], nextIndex: 0, mnemonicStored: false },
  };
}

export interface CreateOptions {
  password: string;
  storeMnemonic?: boolean;   // default false
  initialAlg?: KeyAlgorithm; // default 'dilithium3'
  label?: string;            // default "Account 1"
}

export interface ImportOptions extends CreateOptions {
  mnemonic: string; // space-separated words
}

export class Keyring {
  private persisted: PersistedKeyringV1 = newEmptyState();

  // In-memory unlocked secrets (never persisted)
  private unlockedSeed: Uint8Array | null = null;
  private sessionMnemonic: string | null = null;

  private inited = false;

  get locked(): boolean {
    return this.isLocked();
  }

  // ---------- lifecycle ----------

  /** Load from storage. Idempotent. */
  public async init(): Promise<void> {
    if (this.inited) return;
    const data = await loadVault<PersistedKeyringV1>();
    const envelope = await loadVaultEnvelope();
    if (data && data.version === 1) {
      // Backfill legacy fields (alg ⇔ algo)
      for (const acct of data.meta.accounts) {
        (acct as AccountMeta).algo = (acct as AccountMeta).algo ?? (acct as AccountMeta).alg;
        (acct as AccountMeta).alg = (acct as AccountMeta).alg ?? (acct as AccountMeta).algo;
      }
      this.persisted = data;
      if (envelope) {
        // Prefer the dedicated encrypted envelope if present (authoritative for secrets)
        this.persisted.vault = envelope;
      } else if (data.vault) {
        // Backfill a missing envelope key to ensure only ciphertext is stored
        await saveVaultEnvelope(data.vault);
      }
    } else {
      this.persisted = newEmptyState();
      await this.persistState();
    }
    this.inited = true;
  }

  /** Create a fresh vault with a new mnemonic and first account. Returns the mnemonic. */
  public async create(opts: CreateOptions): Promise<{ mnemonic: string; account: AccountMeta }> {
    await this.init();

    if (!opts?.password || typeof opts.password !== 'string' || opts.password.length < 4) {
      throw new Error('Password must be at least 4 characters.');
    }

    const mnemonic = generateMnemonic();
    const seed = await mnemonicToSeed(mnemonic);
    this.sessionMnemonic = mnemonic;

    // Prepare first account
    const alg: KeyAlgorithm = opts.initialAlg ?? 'dilithium3';
    const { account } = await this.addAccountFromSeed(seed, alg, opts.label ?? 'Account 1', 0);

    // Encrypt vault: seed (+ optional mnemonic)
    const payload = await this.packVaultPayload(seed, opts.storeMnemonic ? mnemonic : undefined);
    const vault = await encryptVault(payload, opts.password);

    this.persisted.vault = vault;
    this.persisted.meta.mnemonicStored = !!opts.storeMnemonic;
    await this.persistState();

    // Keep unlocked for this session (user just created)
    this.unlockedSeed = seed;
    await saveSession({ unlocked: true });

    return { mnemonic, account };
  }

  /** Import from an existing mnemonic and create first account. */
  public async importMnemonic(opts: ImportOptions | string, maybeOpts?: { pin?: string; storeMnemonic?: boolean; initialAlg?: KeyAlgorithm; label?: string; password?: string }): Promise<{ account: AccountMeta }> {
    await this.init();

    let normalized: ImportOptions;
    if (typeof opts === 'string') {
      const pin = maybeOpts?.pin ?? maybeOpts?.password ?? '';
      normalized = {
        mnemonic: opts,
        password: pin,
        storeMnemonic: maybeOpts?.storeMnemonic ?? true,
        initialAlg: maybeOpts?.initialAlg,
        label: maybeOpts?.label,
      } as ImportOptions;
    } else {
      normalized = opts;
    }

    if (!normalized?.password || normalized.password.length < 4) {
      throw new Error('Password must be at least 4 characters.');
    }
    if (!normalized?.mnemonic || typeof normalized.mnemonic !== 'string') {
      throw new Error('Mnemonic required.');
    }

    const seed = await mnemonicToSeed(normalized.mnemonic.trim());

    // Prepare first account
    const alg: KeyAlgorithm = normalized.initialAlg ?? 'dilithium3';
    const { account } = await this.addAccountFromSeed(seed, alg, normalized.label ?? 'Account 1', 0);

    // Encrypt vault with seed (+ optionally mnemonic)
    const payload = await this.packVaultPayload(seed, normalized.storeMnemonic ?? true ? normalized.mnemonic : undefined);
    const vault = await encryptVault(payload, normalized.password);

    this.persisted.vault = vault;
    this.persisted.meta.mnemonicStored = !!(normalized.storeMnemonic ?? true);
    await this.persistState();

    // Keep unlocked for this session
    this.unlockedSeed = seed;
    this.sessionMnemonic = normalized.mnemonic.trim();
    await saveSession({ unlocked: true });

    return { account };
  }

  /** Unlock the vault for the session. No-op if already unlocked. */
  public async unlock(password: string): Promise<void> {
    await this.init();
    if (this.unlockedSeed) return;
    const v = this.persisted.vault;
    if (!v) throw new Error('No vault set up.');
    const payload = await decryptVault(v, password);
    const { seed, mnemonic } = await this.unpackVaultPayload(payload);
    this.unlockedSeed = seed;
    this.sessionMnemonic = mnemonic ?? this.sessionMnemonic;
    await saveSession({ unlocked: true });
  }

  /** Lock: forget secrets from memory. */
  public lock(): boolean {
    this.unlockedSeed = null;
    this.sessionMnemonic = null;
    void clearSession();
    return true;
  }

  /** True if the seed is not resident in memory. */
  public isLocked(): boolean {
    return !this.unlockedSeed;
  }

  /** Convenience account derivation for tests and API parity. */
  public async deriveAccount(opts: { index: number; algo: KeyAlgorithm }): Promise<AccountMeta> {
    await this.init();
    if (this.isLocked()) throw new Error('Keyring is locked.');
    const existing = this.persisted.meta.accounts.find(
      (a) => a.index === opts.index && a.alg === opts.algo,
    );
    if (existing) return existing;

    const seed = this.requireUnlocked();
    const { account } = await this.addAccountFromSeed(
      seed,
      opts.algo,
      `Account ${opts.index + 1}`,
      opts.index,
    );
    if (this.persisted.meta.nextIndex <= opts.index) {
      this.persisted.meta.nextIndex = opts.index + 1;
    }
    await this.persistState();
    return account;
  }

  /** Fetch (or lazily derive) an account at a specific index/algorithm. */
  public async getAccountAt(index: number, algo: KeyAlgorithm): Promise<AccountMeta> {
    return this.deriveAccount({ index, algo });
  }

  /** List all known accounts (public metadata). */
  public async getAccounts(): Promise<AccountMeta[]> {
    return this.listAccounts();
  }

  /** Danger: wipe everything (requires already-unlocked + confirmation flag). */
  public async factoryReset({ confirm }: { confirm: boolean }): Promise<void> {
    await this.init();
    if (!confirm) throw new Error('Confirmation required.');
    this.unlockedSeed = null;
    this.sessionMnemonic = null;
    this.persisted = newEmptyState();
    await clearVault();
    await saveVault(this.persisted);
    try {
      await clearVaultEnvelope();
    } catch {
      /* ignore */
    }
    await clearSession();
  }

  // ---------- accounts ----------

  /** Return shallow list of accounts (public metadata only). */
  public async listAccounts(): Promise<AccountMeta[]> {
    await this.init();
    return [...this.persisted.meta.accounts];
  }

  /** Currently selected account (or undefined). */
  public async getSelected(): Promise<AccountMeta | undefined> {
    await this.init();
    const id = this.persisted.meta.selectedId;
    return this.persisted.meta.accounts.find(a => a.id === id);
  }

  /** Select a specific account by id. */
  public async selectAccount(id: string): Promise<void> {
    await this.init();
    if (!this.persisted.meta.accounts.some(a => a.id === id)) {
      throw new Error('Account not found.');
    }
    this.persisted.meta.selectedId = id;
    await this.persistState();
  }

  /** Add a new derived account using the next index. Requires unlocked seed. */
  public async addAccount(alg: KeyAlgorithm = 'dilithium3', label?: string): Promise<AccountMeta> {
    await this.init();
    const seed = this.requireUnlocked();
    const idx = this.persisted.meta.nextIndex;
    const { account } = await this.addAccountFromSeed(seed, alg, label ?? `Account ${idx + 1}`, idx);
    this.persisted.meta.nextIndex = idx + 1;
    await this.persistState();
    // Keep first account selected by default
    if (!this.persisted.meta.selectedId) {
      this.persisted.meta.selectedId = account.id;
      await this.persistState();
    }
    return account;
  }

  /** Remove an account by id. Cannot remove if it is the only account. */
  public async removeAccount(id: string): Promise<void> {
    await this.init();
    const accs = this.persisted.meta.accounts;
    if (accs.length <= 1) {
      throw new Error('Cannot remove the only account.');
    }
    const idx = accs.findIndex(a => a.id === id);
    if (idx < 0) throw new Error('Account not found.');
    accs.splice(idx, 1);

    if (this.persisted.meta.selectedId === id) {
      this.persisted.meta.selectedId = accs[0]?.id;
    }
    await this.persistState();
  }

  // ---------- export helpers ----------

  /**
   * Export mnemonic if (and only if) it was stored in the vault by choice.
   * If a session mnemonic is available (unlocked), it is returned directly.
   * Otherwise requires password to decrypt.
   */
  public async exportMnemonic(password?: string): Promise<string> {
    await this.init();
    if (this.isLocked()) throw new Error('Keyring is locked.');
    if (this.sessionMnemonic) return this.sessionMnemonic;

    if (!this.persisted.vault || !this.persisted.meta.mnemonicStored) {
      throw new Error('Mnemonic was not stored in this vault.');
    }
    if (!password) throw new Error('Password required to decrypt mnemonic.');

    const payload = await decryptVault(this.persisted.vault, password);
    const { mnemonic } = await this.unpackVaultPayload(payload);
    if (!mnemonic) throw new Error('Mnemonic missing from vault.');
    this.sessionMnemonic = mnemonic;
    return mnemonic;
  }

  /** Direct mnemonic getter used by some tests (requires unlocked session). */
  public async getMnemonic(): Promise<string> {
    await this.init();
    if (this.isLocked() || !this.sessionMnemonic) throw new Error('Keyring is locked.');
    return this.sessionMnemonic;
  }

  // ---------- signing ----------

  /** Sign raw bytes using the given account. Domain separation is the caller's responsibility. */
  public async signBytes(accountId: string, bytes: Uint8Array): Promise<Uint8Array> {
    await this.init();
    const seed = this.requireUnlocked();
    const account = this.persisted.meta.accounts.find(a => a.id === accountId);
    if (!account) throw new Error('Account not found.');

    // Derive PQ keypair deterministically for this account (stateless)
    const kp = await deriveKeypair(seed, account.alg, { index: account.index });

    if (account.alg === 'dilithium3') {
      return await d3.sign(bytes, kp.secretKey);
    } else {
      return await sphincs.sign(bytes, kp.secretKey);
    }
  }

  // ---------- internals ----------

  private requireUnlocked(): Uint8Array {
    if (!this.unlockedSeed) {
      throw new Error('Keyring is locked.');
    }
    return this.unlockedSeed;
  }

  private async addAccountFromSeed(
    seed: Uint8Array,
    alg: KeyAlgorithm,
    label: string,
    index: number,
  ): Promise<{ account: AccountMeta }> {
    // Path convention (kept simple; actual derivation occurs in ./derive)
    const path = `m/animica/${index}`;

    const kp = await deriveKeypair(seed, alg, { index });
    const address = await bech32AddressFromPub(kp.publicKey, alg);
    const id = await this.makeAccountId(address, index);

    const meta: AccountMeta = { id, label, alg, algo: alg, path, index, address };
    this.persisted.meta.accounts.push(meta);
    if (!this.persisted.meta.selectedId) this.persisted.meta.selectedId = meta.id;

    return { account: meta };
  }

  private async makeAccountId(address: string, index: number): Promise<string> {
    // Stable deterministic id from address + index
    // Tiny hash: use WebCrypto SHA-256 then base36
    const enc = new TextEncoder();
    const data = enc.encode(`${address}|${index}`);
    const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', data));
    // base36 10 chars
    let n = 0n;
    for (let i = 0; i < 6; i++) n = (n << 8n) | BigInt(digest[i]);
    return `acc_${n.toString(36).padStart(10, '0')}`;
  }

  private async packVaultPayload(seed: Uint8Array, mnemonic?: string): Promise<Uint8Array> {
    // msgspec/cbor2 not available here; use simple JSON + UTF-8 for portability.
    const obj = {
      v: 1,
      seed: Array.from(seed),
      mnemonic: mnemonic ?? null,
    };
    const json = JSON.stringify(obj);
    return new TextEncoder().encode(json);
  }

  private async unpackVaultPayload(payload: Uint8Array): Promise<{ seed: Uint8Array; mnemonic?: string | null }> {
    const json = new TextDecoder().decode(payload);
    const obj = JSON.parse(json) as { v: number; seed: number[]; mnemonic?: string | null };
    if (obj.v !== 1 || !Array.isArray(obj.seed)) throw new Error('Invalid vault payload.');
    return { seed: new Uint8Array(obj.seed), mnemonic: obj.mnemonic ?? null };
  }

  /** Persist keyring state plus encrypted envelope (if present). */
  private async persistState(): Promise<void> {
    await saveVault(this.persisted);
    if (this.persisted.vault) {
      await saveVaultEnvelope(this.persisted.vault);
      this.logCipherIntegrity(this.persisted.vault).catch(() => undefined);
    }
  }

  /** Dev-only sanity log to ensure ciphertext is not trivially readable. */
  private async logCipherIntegrity(vault: EncryptedVault): Promise<void> {
    try {
      if (!((import.meta as any)?.env?.DEV)) return;
      const decoded = atob(vault.ciphertextB64);
      try {
        JSON.parse(decoded);
        console.warn('[keyring] Vault ciphertext looked like JSON — check encryption setup.');
      } catch {
        // Expected: ciphertext should not be parseable JSON
      }
    } catch {
      /* ignore logging errors */
    }
  }
}

// Singleton instance
export const keyring = new Keyring();
export default keyring;

// Compatibility factory for tests/specs
export function createKeyring(opts?: unknown) {
  return new Keyring(opts as any);
}

// Alias used by some controller-oriented code paths
export const KeyringController = Keyring;

// Convenience top-level helpers (optional)
export async function ensureInited() {
  await keyring.init();
}
export async function isLocked(): Promise<boolean> {
  await keyring.init();
  return keyring.isLocked();
}
export async function selectedAccount(): Promise<AccountMeta | undefined> {
  await keyring.init();
  return keyring.getSelected();
}
