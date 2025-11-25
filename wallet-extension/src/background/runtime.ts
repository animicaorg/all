/**
 * Thin wrappers over Chrome MV3 runtime, alarms, and storage with Promise APIs.
 * These helpers keep the rest of the background code clean & testable.
 */

/* eslint-disable no-console */

type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

// In Firefox MV3 or test envs, a "browser" global may exist.
// Normalize to a single "chromeLike" surface for convenience.
const chromeLike: typeof chrome =
  (globalThis as any).chrome ??
  (globalThis as any).browser ??
  (() => {
    // Very small mock for unit tests (only methods we use get stubbed in tests).
    const mock: any = {};
    mock.runtime = {
      id: 'test-extension-id',
      getManifest: () => ({ version: '0.0.0-test' }),
      getURL: (p: string) => `chrome-extension://test/${p}`,
    };
    mock.alarms = {
      create: () => void 0,
      clear: (_?: string) => Promise.resolve(true),
      clearAll: () => Promise.resolve(),
      get: (_: string) => Promise.resolve(undefined),
      getAll: () => Promise.resolve([]),
      onAlarm: { addListener: () => void 0 },
    };
    mock.storage = {
      local: {
        get: (_?: any, cb?: any) => cb?.({}) ?? Promise.resolve({}),
        set: (_: any, cb?: any) => cb?.() ?? Promise.resolve(),
        remove: (_: any, cb?: any) => cb?.() ?? Promise.resolve(),
      },
      onChanged: { addListener: () => void 0 },
    };
    mock.runtime.onMessage = { addListener: () => void 0 };
    mock.runtime.onConnect = { addListener: () => void 0 };
    mock.runtime.getPlatformInfo = (cb: any) => cb?.({ os: 'mac', arch: 'x86-64', nacl_arch: 'x86-64' });
    return mock as typeof chrome;
  })();

export const runtime = {
  id(): string {
    return chromeLike.runtime.id;
  },
  manifest(): chrome.runtime.ManifestV3 {
    return chromeLike.runtime.getManifest() as chrome.runtime.ManifestV3;
  },
  version(): string {
    return (chromeLike.runtime.getManifest() as chrome.runtime.ManifestV3).version ?? '0.0.0';
  },
  getURL(path: string): string {
    return chromeLike.runtime.getURL(path);
  },
  getPlatformInfo(): Promise<chrome.runtime.PlatformInfo> {
    return new Promise((resolve, reject) => {
      try {
        chromeLike.runtime.getPlatformInfo((info) => resolve(info));
      } catch (e) {
        reject(e);
      }
    });
  },
};

/* ------------------------------ Alarms API ------------------------------ */

export type AlarmName = string;

export interface PeriodicOpts {
  /** Minutes, MV3 minimum is 1 */
  periodInMinutes: number;
  /** First run time override (Date.now() + ms), if omitted starts after period */
  whenMs?: number;
}
export interface OneShotOpts {
  /** Fire at a specific epoch ms */
  whenMs: number;
}

export const alarms = {
  createPeriodic(name: AlarmName, opts: PeriodicOpts): void {
    const { periodInMinutes, whenMs } = opts;
    if (whenMs != null) {
      chromeLike.alarms.create(name, { when: whenMs, periodInMinutes });
    } else {
      chromeLike.alarms.create(name, { periodInMinutes });
    }
  },

  createOnce(name: AlarmName, opts: OneShotOpts): void {
    chromeLike.alarms.create(name, { when: opts.whenMs });
  },

  clear(name: AlarmName): Promise<boolean> {
    return chromeLike.alarms.clear(name);
  },

  clearAll(): Promise<void> {
    return chromeLike.alarms.clearAll();
  },

  get(name: AlarmName): Promise<chrome.alarms.Alarm | undefined> {
    return chromeLike.alarms.get(name);
  },

  getAll(): Promise<chrome.alarms.Alarm[]> {
    return chromeLike.alarms.getAll();
  },
};

/* ----------------------------- Storage API ------------------------------ */

/**
 * Namespaced K/V view over chrome.storage.local
 * Automatically prefixes keys and JSON-encodes values for durability.
 */
export interface NamespaceStorage {
  get<T extends Json = Json>(key: string, defaultValue?: T): Promise<T | undefined>;
  getMany<T extends Record<string, Json>>(keys: (keyof T & string)[], defaults?: Partial<T>): Promise<Partial<T>>;
  set<T extends Json = Json>(key: string, value: T): Promise<void>;
  setMany<T extends Record<string, Json>>(entries: T): Promise<void>;
  remove(key: string | string[]): Promise<void>;
  /** Returns all keys under this namespace (best-effort) */
  keys(): Promise<string[]>;
}

function prefix(ns: string, key: string) {
  return `${ns}:${key}`;
}

function unprefix(ns: string, fullKey: string) {
  const p = `${ns}:`;
  return fullKey.startsWith(p) ? fullKey.slice(p.length) : fullKey;
}

/** Raw access to chrome.storage.local (promisified) */
export const rawStorage = {
  async get(keys?: string[] | string | null): Promise<Record<string, any>> {
    return new Promise((resolve, reject) => {
      try {
        (chromeLike.storage.local.get as any)(keys ?? null, (items: any) => {
          const err = (chromeLike.runtime as any).lastError;
          if (err) reject(new Error(err.message));
          else resolve(items ?? {});
        });
      } catch (e) {
        reject(e);
      }
    });
  },
  async set(items: Record<string, any>): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        chromeLike.storage.local.set(items, () => {
          const err = (chromeLike.runtime as any).lastError;
          if (err) reject(new Error(err.message));
          else resolve();
        });
      } catch (e) {
        reject(e);
      }
    });
  },
  async remove(keys: string | string[]): Promise<void> {
    return new Promise((resolve, reject) => {
      try {
        chromeLike.storage.local.remove(keys as any, () => {
          const err = (chromeLike.runtime as any).lastError;
          if (err) reject(new Error(err.message));
          else resolve();
        });
      } catch (e) {
        reject(e);
      }
    });
  },
};

export function namespacedStorage(ns: string): NamespaceStorage {
  return {
    async get<T extends Json>(key: string, defaultValue?: T) {
      const res = await rawStorage.get(prefix(ns, key));
      const raw = res[prefix(ns, key)];
      if (raw === undefined) return defaultValue;
      try {
        return JSON.parse(raw) as T;
      } catch {
        // If older versions stored plain value, return as is
        return raw as T;
      }
    },

    async getMany<T extends Record<string, Json>>(keys: (keyof T & string)[], defaults?: Partial<T>) {
      const fullKeys = keys.map((k) => prefix(ns, k));
      const res = await rawStorage.get(fullKeys);
      const out: Partial<T> = { ...(defaults ?? {}) } as Partial<T>;
      for (const fk of Object.keys(res)) {
        const k = unprefix(ns, fk) as keyof T & string;
        try {
          (out as any)[k] = JSON.parse(res[fk]);
        } catch {
          (out as any)[k] = res[fk];
        }
      }
      // Fill missing from defaults
      for (const k of keys) {
        if (!(k in out) && defaults && k in defaults) {
          (out as any)[k] = (defaults as any)[k];
        }
      }
      return out;
    },

    async set<T extends Json>(key: string, value: T) {
      const payload: Record<string, string> = {};
      payload[prefix(ns, key)] = JSON.stringify(value);
      await rawStorage.set(payload);
    },

    async setMany<T extends Record<string, Json>>(entries: T) {
      const payload: Record<string, string> = {};
      for (const [k, v] of Object.entries(entries)) {
        payload[prefix(ns, k)] = JSON.stringify(v as Json);
      }
      await rawStorage.set(payload);
    },

    async remove(key: string | string[]) {
      if (Array.isArray(key)) {
        await rawStorage.remove(key.map((k) => prefix(ns, k)));
      } else {
        await rawStorage.remove(prefix(ns, key));
      }
    },

    async keys(): Promise<string[]> {
      const all = await rawStorage.get(null);
      return Object.keys(all)
        .filter((k) => k.startsWith(`${ns}:`))
        .map((k) => unprefix(ns, k));
    },
  };
}

/* --------------------------- Messaging helpers -------------------------- */

export interface BgRequest<Payload = unknown> {
  route: string;
  payload?: Payload;
}

export interface BgResponse<Result = unknown> {
  ok: boolean;
  result?: Result;
  error?: string;
}

export async function sendMessage<Req extends BgRequest, Res = unknown>(
  req: Req
): Promise<Res> {
  return new Promise<Res>((resolve, reject) => {
    try {
      chromeLike.runtime.sendMessage(req, (resp: BgResponse<Res>) => {
        const err = (chromeLike.runtime as any).lastError;
        if (err) {
          reject(new Error(err.message));
          return;
        }
        if (!resp) {
          reject(new Error('No response from background'));
          return;
        }
        if (resp.ok) resolve(resp.result as Res);
        else reject(new Error(resp.error || 'Unknown background error'));
      });
    } catch (e) {
      reject(e);
    }
  });
}

export function connectPort(name: string): chrome.runtime.Port {
  return chromeLike.runtime.connect({ name });
}

/* ------------------------------- Utilities ------------------------------ */

export const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

export async function withTimeout<T>(p: Promise<T>, ms: number, label = 'timeout'): Promise<T> {
  let to: any;
  const timeout = new Promise<never>((_, rej) => (to = setTimeout(() => rej(new Error(label)), ms)));
  try {
    return await Promise.race([p, timeout]);
  } finally {
    clearTimeout(to);
  }
}

export function isMV3(): boolean {
  const mf = runtime.manifest();
  return (mf as any).manifest_version === 3;
}

/**
 * Subscribe to storage changes for a namespace; returns an unsubscribe.
 */
export function onNamespaceChanged(
  ns: string,
  cb: (keys: string[]) => void
): () => void {
  const handler = (
    changes: { [key: string]: chrome.storage.StorageChange },
    areaName: 'sync' | 'local' | 'managed' | 'session'
  ) => {
    if (areaName !== 'local') return;
    const touched = Object.keys(changes)
      .filter((k) => k.startsWith(`${ns}:`))
      .map((k) => unprefix(ns, k));
    if (touched.length) cb(touched);
  };
  chromeLike.storage.onChanged.addListener(handler);
  return () => chromeLike.storage.onChanged.removeListener(handler);
}

export default {
  runtime,
  alarms,
  rawStorage,
  namespacedStorage,
  sendMessage,
  connectPort,
  delay,
  withTimeout,
  isMV3,
  onNamespaceChanged,
};
