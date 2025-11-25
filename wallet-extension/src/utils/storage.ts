/**
 * Namespaced storage helpers.
 *
 * Uses chrome.storage.(local|session) when available (MV3 background/UI/content),
 * otherwise falls back to window.localStorage (for unit/E2E in plain browsers)
 * and finally to an in-memory Map (for node/vitest).
 *
 * Values should be structured-cloneable (objects, arrays, numbers, strings, booleans).
 * When using the localStorage or memory fallback we JSON.stringify/parse values.
 */

export type StorageAreaKind = 'local' | 'session' | 'memory';

export interface StorageChange<T = unknown> {
  key: string;
  oldValue: T | undefined;
  newValue: T | undefined;
}

export interface IStorageArea {
  get<T = unknown>(fullKey: string): Promise<T | undefined>;
  set<T = unknown>(fullKey: string, value: T): Promise<void>;
  remove(fullKey: string): Promise<void>;
  getAll<T = unknown>(prefix?: string): Promise<Record<string, T>>;
  clear(): Promise<void>;
  onChange?(cb: (change: StorageChange) => void): () => void;
}

function hasChromeStorage(): boolean {
  try {
    // @ts-ignore
    return !!globalThis.chrome?.storage;
  } catch {
    return false;
  }
}

/* ---------------------------- Chrome storage area --------------------------- */

class ChromeArea implements IStorageArea {
  private area: chrome.storage.StorageArea;
  private areaName: 'local' | 'session';

  constructor(area: 'local' | 'session' = 'local') {
    // @ts-ignore
    const chromeStorage = globalThis.chrome?.storage;
    if (!chromeStorage) throw new Error('chrome.storage not available');
    this.area = area === 'local' ? chromeStorage.local : (chromeStorage.session ?? chromeStorage.local);
    this.areaName = area;
  }

  async get<T = unknown>(fullKey: string): Promise<T | undefined> {
    return new Promise((resolve, reject) => {
      this.area.get([fullKey], (result) => {
        const err = chrome.runtime?.lastError;
        if (err) return reject(new Error(err.message));
        resolve(result[fullKey] as T | undefined);
      });
    });
  }

  async set<T = unknown>(fullKey: string, value: T): Promise<void> {
    return new Promise((resolve, reject) => {
      this.area.set({ [fullKey]: value as any }, () => {
        const err = chrome.runtime?.lastError;
        if (err) return reject(new Error(err.message));
        resolve();
      });
    });
  }

  async remove(fullKey: string): Promise<void> {
    return new Promise((resolve, reject) => {
      this.area.remove(fullKey, () => {
        const err = chrome.runtime?.lastError;
        if (err) return reject(new Error(err.message));
        resolve();
      });
    });
  }

  async getAll<T = unknown>(prefix?: string): Promise<Record<string, T>> {
    return new Promise((resolve, reject) => {
      this.area.get(null, (all) => {
        const err = chrome.runtime?.lastError;
        if (err) return reject(new Error(err.message));
        const out: Record<string, T> = {};
        Object.keys(all).forEach((k) => {
          if (!prefix || k.startsWith(prefix)) {
            out[k] = all[k] as T;
          }
        });
        resolve(out);
      });
    });
  }

  async clear(): Promise<void> {
    return new Promise((resolve, reject) => {
      this.area.clear(() => {
        const err = chrome.runtime?.lastError;
        if (err) return reject(new Error(err.message));
        resolve();
      });
    });
  }

  onChange?(cb: (change: StorageChange) => void): () => void {
    const handler = (
      changes: { [key: string]: chrome.storage.StorageChange },
      areaName: 'local' | 'sync' | 'managed' | 'session'
    ) => {
      if ((this.areaName === 'session' && areaName !== 'session') || (this.areaName === 'local' && areaName !== 'local')) {
        return;
      }
      for (const [key, c] of Object.entries(changes)) {
        cb({ key, oldValue: c.oldValue as any, newValue: c.newValue as any });
      }
    };
    chrome.storage.onChanged.addListener(handler);
    return () => chrome.storage.onChanged.removeListener(handler);
  }
}

/* ------------------------- localStorage / memory fallbacks ------------------ */

class LocalStorageArea implements IStorageArea {
  private ls: Storage;
  constructor(ls: Storage = globalThis.localStorage) {
    if (!ls) throw new Error('localStorage not available');
    this.ls = ls;
  }

  async get<T = unknown>(fullKey: string): Promise<T | undefined> {
    const s = this.ls.getItem(fullKey);
    if (s == null) return undefined;
    try {
      return JSON.parse(s) as T;
    } catch {
      // Backward-compat for raw strings
      return s as any;
    }
  }

  async set<T = unknown>(fullKey: string, value: T): Promise<void> {
    const s = JSON.stringify(value);
    this.ls.setItem(fullKey, s);
  }

  async remove(fullKey: string): Promise<void> {
    this.ls.removeItem(fullKey);
  }

  async getAll<T = unknown>(prefix?: string): Promise<Record<string, T>> {
    const out: Record<string, T> = {};
    for (let i = 0; i < this.ls.length; i++) {
      const k = this.ls.key(i)!;
      if (!prefix || k.startsWith(prefix)) {
        const v = await this.get<T>(k);
        if (typeof v !== 'undefined') out[k] = v;
      }
    }
    return out;
  }

  async clear(): Promise<void> {
    this.ls.clear();
  }
}

class MemoryArea implements IStorageArea {
  private m = new Map<string, unknown>();

  async get<T = unknown>(fullKey: string): Promise<T | undefined> {
    return this.m.get(fullKey) as T | undefined;
  }

  async set<T = unknown>(fullKey: string, value: T): Promise<void> {
    this.m.set(fullKey, value);
  }

  async remove(fullKey: string): Promise<void> {
    this.m.delete(fullKey);
  }

  async getAll<T = unknown>(prefix?: string): Promise<Record<string, T>> {
    const out: Record<string, T> = {};
    for (const [k, v] of this.m.entries()) {
      if (!prefix || k.startsWith(prefix)) out[k] = v as T;
    }
    return out;
  }

  async clear(): Promise<void> {
    this.m.clear();
  }
}

/* --------------------------------- Factory ---------------------------------- */

function pickArea(kind: StorageAreaKind = 'local'): IStorageArea {
  if (kind === 'memory') return new MemoryArea();
  if (hasChromeStorage()) return new ChromeArea(kind === 'session' ? 'session' : 'local');
  try {
    if (globalThis.localStorage) return new LocalStorageArea(globalThis.localStorage);
  } catch {
    // ignore
  }
  return new MemoryArea();
}

export interface NamespacedStorage {
  /** Get value for key (without namespace prefix). */
  get<T = unknown>(key: string): Promise<T | undefined>;
  /** Set value for key. */
  set<T = unknown>(key: string, value: T): Promise<void>;
  /** Remove key. */
  remove(key: string): Promise<void>;
  /** Get all entries in the namespace (returns map without namespace in keys). */
  getAll<T = unknown>(): Promise<Record<string, T>>;
  /** Clear only keys in this namespace. */
  clear(): Promise<void>;
  /** Subscribe to changes within this namespace. Returns unsubscribe. */
  onChange?(cb: (change: StorageChange) => void): () => void;
  /** Build fully-qualified key: ns:key */
  fullKey(key: string): string;
  /** Expose raw area for advanced use. */
  _raw: IStorageArea;
  /** Namespace string */
  namespace: string;
}

/**
 * Create a namespaced storage using selected area.
 * Keys are stored as `${namespace}:${key}` to avoid collisions.
 */
export function createStorage(namespace: string, area: StorageAreaKind = 'local'): NamespacedStorage {
  if (!namespace || namespace.includes(':')) {
    throw new Error('namespace must be a non-empty string without ":"');
  }
  const raw = pickArea(area);
  const prefix = `${namespace}:`;

  const nsStorage: NamespacedStorage = {
    namespace,
    _raw: raw,
    fullKey: (k: string) => prefix + k,

    async get<T = unknown>(key: string) {
      return raw.get<T>(prefix + key);
    },

    async set<T = unknown>(key: string, value: T) {
      return raw.set(prefix + key, value);
    },

    async remove(key: string) {
      return raw.remove(prefix + key);
    },

    async getAll<T = unknown>() {
      const all = await raw.getAll<T>(prefix);
      const out: Record<string, T> = {};
      for (const [k, v] of Object.entries(all)) {
        if (k.startsWith(prefix)) out[k.slice(prefix.length)] = v;
      }
      return out;
    },

    async clear() {
      const all = await raw.getAll(prefix);
      await Promise.all(Object.keys(all).map((fk) => raw.remove(fk)));
    },

    onChange: raw.onChange
      ? (cb: (change: StorageChange) => void) => {
          return raw.onChange!((chg) => {
            if (chg.key.startsWith(prefix)) {
              cb({
                key: chg.key.slice(prefix.length),
                oldValue: chg.oldValue,
                newValue: chg.newValue,
              });
            }
          });
        }
      : undefined,
  };

  return nsStorage;
}

/* ------------------------------- Convenience ------------------------------- */

/** Shorthand helpers for common namespaces used across the extension. */
export const storageNamespaces = {
  keyring: 'keyring',
  sessions: 'sessions',
  permissions: 'permissions',
  settings: 'settings',
  network: 'network',
  cache: 'cache',
} as const;

export type KnownNamespace = typeof storageNamespaces[keyof typeof storageNamespaces];

export function getKeyringStorage(area: StorageAreaKind = 'local') {
  return createStorage(storageNamespaces.keyring, area);
}
export function getSessionsStorage(area: StorageAreaKind = 'session') {
  return createStorage(storageNamespaces.sessions, area);
}
export function getPermissionsStorage(area: StorageAreaKind = 'local') {
  return createStorage(storageNamespaces.permissions, area);
}
export function getSettingsStorage(area: StorageAreaKind = 'local') {
  return createStorage(storageNamespaces.settings, area);
}
export function getNetworkStorage(area: StorageAreaKind = 'local') {
  return createStorage(storageNamespaces.network, area);
}
export function getCacheStorage(area: StorageAreaKind = 'local') {
  return createStorage(storageNamespaces.cache, area);
}

export default {
  createStorage,
  storageNamespaces,
  getKeyringStorage,
  getSessionsStorage,
  getPermissionsStorage,
  getSettingsStorage,
  getNetworkStorage,
  getCacheStorage,
};
