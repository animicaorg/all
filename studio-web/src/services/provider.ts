/**
 * Provider detection & session management for the Animica wallet-extension.
 *
 * This follows an EIP-1193-like shape:
 *   - window.animica?.request({ method, params })
 *   - events: 'accountsChanged', 'chainChanged', 'connect', 'disconnect', 'newHeads'
 *
 * We keep this file framework-agnostic. Zustand slices can import these helpers.
 */

export class ProviderNotFoundError extends Error {
  constructor(msg = 'Animica provider not found') {
    super(msg);
    this.name = 'ProviderNotFoundError';
  }
}

export type RequestArguments = {
  method: string;
  params?: unknown[] | Record<string, unknown>;
};

export type AccountsChangedHandler = (accounts: string[]) => void;
export type ChainChangedHandler = (chainId: number) => void;
export type NewHeadsHandler = (head: { height: number; hash: string; ts?: number }) => void;

export interface AnimicaProvider {
  isAnimica?: boolean; // optional feature flag set by wallet-extension
  request<T = unknown>(args: RequestArguments): Promise<T>;
  on?(event: 'accountsChanged', handler: AccountsChangedHandler): void;
  on?(event: 'chainChanged', handler: ChainChangedHandler): void;
  on?(event: 'newHeads', handler: NewHeadsHandler): void;
  on?(event: 'connect' | 'disconnect', handler: (...args: any[]) => void): void;
  removeListener?(event: string, handler: (...args: any[]) => void): void;
}

declare global {
  interface Window {
    animica?: AnimicaProvider;
    // for compatibility if running alongside other providers
    ethereum?: { request(args: RequestArguments): Promise<any>; on?: (e: string, cb: any) => void };
  }
}

/** Narrow a value to AnimicaProvider if it looks like one. */
export function isAnimicaProvider(x: any): x is AnimicaProvider {
  return !!x && typeof x.request === 'function';
}

/** Return the provider synchronously if already present. */
export function getProviderSync(): AnimicaProvider | undefined {
  if (isAnimicaProvider((globalThis as any).animica)) return (globalThis as any).animica!;
  return undefined;
}

let detectionPromise: Promise<AnimicaProvider> | null = null;

/**
 * Wait for window.animica to appear. Resolves fast if already injected.
 * Listens for a custom 'animica#initialized' event (mirroring MetaMask pattern)
 * and falls back to DOMContentLoaded + a short polling loop.
 */
export function waitForProvider(timeoutMs = 5000): Promise<AnimicaProvider> {
  if (detectionPromise) return withTimeout(detectionPromise, timeoutMs, new ProviderNotFoundError());

  detectionPromise = new Promise<AnimicaProvider>((resolve) => {
    const existing = getProviderSync();
    if (existing) return resolve(existing);

    const onReady = () => {
      const p = getProviderSync();
      if (p) resolve(p);
    };

    // 1) Custom initialization event (extension may dispatch this)
    globalThis.addEventListener?.('animica#initialized' as any, onReady, { once: true });

    // 2) DOM ready fallback
    if (document.readyState === 'complete' || document.readyState === 'interactive') {
      // small microtask to allow content-script injection
      queueMicrotask(onReady);
    } else {
      window.addEventListener('DOMContentLoaded', onReady, { once: true });
    }

    // 3) Short polling fallback (covers odd injection timing)
    let tries = 0;
    const poll = () => {
      const p = getProviderSync();
      if (p) return resolve(p);
      if (tries++ < 50) setTimeout(poll, 50);
    };
    poll();
  });

  return withTimeout(detectionPromise, timeoutMs, new ProviderNotFoundError());
}

/** Get a provider (sync if available, else await injection). */
export async function getProvider(timeoutMs = 5000): Promise<AnimicaProvider> {
  const p = getProviderSync();
  if (p) return p;
  return waitForProvider(timeoutMs);
}

/** Perform a .request() with sane error shaping. */
export async function rpc<T = unknown>(
  method: string,
  params?: RequestArguments['params'],
  timeoutMs = 15000
): Promise<T> {
  const p = await getProvider(timeoutMs);
  try {
    return await withTimeout(p.request<T>({ method, params }), timeoutMs, timeoutError(method));
  } catch (err: any) {
    throw shapeProviderError(err, method);
  }
}

export interface ConnectOptions {
  /** If true, do not prompt; just read current permissions if any. */
  silent?: boolean;
  /** Expected chain id; if defined and mismatch -> throws. */
  expectChainId?: number;
  timeoutMs?: number;
}

/**
 * Connect (or silently read) accounts from the provider.
 * Returns connected accounts and the current chain id.
 */
export async function connect(opts: ConnectOptions = {}): Promise<{
  provider: AnimicaProvider;
  accounts: string[];
  chainId: number;
}> {
  const { silent = false, expectChainId, timeoutMs = 30000 } = opts;
  const provider = await getProvider(timeoutMs);

  const accounts = await (async () => {
    try {
      if (silent) {
        // Best-effort silent path
        return await withTimeout(
          provider.request<string[]>({ method: 'animica_getAccounts' }),
          timeoutMs,
          timeoutError('animica_getAccounts')
        );
      }
      return await withTimeout(
        provider.request<string[]>({ method: 'animica_requestAccounts' }),
        timeoutMs,
        timeoutError('animica_requestAccounts')
      );
    } catch (e: any) {
      // Compatibility fallback for dapps that accidentally call eth_* in a multi-wallet context
      if (!silent && e?.code === -32601 /* method not found */ && window.ethereum) {
        try {
          const ethAccs = await window.ethereum.request({ method: 'eth_requestAccounts' });
          if (Array.isArray(ethAccs) && ethAccs.length) return ethAccs as string[];
        } catch { /* ignore */ }
      }
      throw shapeProviderError(e, silent ? 'animica_getAccounts' : 'animica_requestAccounts');
    }
  })();

  const chainId = await getChainId(provider, timeoutMs);

  if (typeof expectChainId === 'number' && chainId !== expectChainId) {
    throw new Error(`Connected to chainId=${chainId}, expected ${expectChainId}`);
  }

  // persist lightweight session
  try {
    saveSession({ address: accounts[0], chainId });
  } catch { /* storage may be blocked */ }

  return { provider, accounts, chainId };
}

/** Disconnect simply clears local session; user revokes inside the wallet UI. */
export function disconnect(): void {
  clearSession();
}

/** Subscribe to provider events; returns an unsubscribe function. */
export async function subscribe(
  handlers: Partial<{
    accountsChanged: AccountsChangedHandler;
    chainChanged: ChainChangedHandler;
    newHeads: NewHeadsHandler;
    connect: () => void;
    disconnect: () => void;
  }>
): Promise<() => void> {
  const p = await getProvider();
  const offs: Array<() => void> = [];

  const on = (ev: string, fn: (...a: any[]) => void) => {
    if (!p.on || !p.removeListener) return;
    p.on(ev as any, fn);
    offs.push(() => p.removeListener!(ev, fn));
  };

  if (handlers.accountsChanged) on('accountsChanged', handlers.accountsChanged);
  if (handlers.chainChanged) on('chainChanged', handlers.chainChanged as any);
  if (handlers.newHeads) on('newHeads', handlers.newHeads as any);
  if (handlers.connect) on('connect', handlers.connect);
  if (handlers.disconnect) on('disconnect', handlers.disconnect);

  return () => offs.splice(0).forEach((f) => f());
}

/** Get chain id via multiple strategies; normalized to number. */
export async function getChainId(provider?: AnimicaProvider, timeoutMs = 8000): Promise<number> {
  const p = provider ?? (await getProvider(timeoutMs));
  // Try native method first
  try {
    const v = await withTimeout(p.request<any>({ method: 'animica_chainId' }), timeoutMs, timeoutError('animica_chainId'));
    return normalizeChainId(v);
  } catch { /* next */ }

  // Try direct JSON-RPC method name, if provider proxies
  try {
    const v = await withTimeout(p.request<any>({ method: 'chain.getChainId' }), timeoutMs, timeoutError('chain.getChainId'));
    return normalizeChainId(v);
  } catch { /* next */ }

  // Last resort eth_chainId compatibility
  try {
    const v = await withTimeout(p.request<any>({ method: 'eth_chainId' }), timeoutMs, timeoutError('eth_chainId'));
    return normalizeChainId(v);
  } catch (e) {
    throw shapeProviderError(e, 'animica_chainId');
  }
}

export function normalizeChainId(x: unknown): number {
  if (typeof x === 'number') return x >>> 0;
  if (typeof x === 'string') {
    const s = x.trim();
    if (s.startsWith('0x') || s.startsWith('0X')) return Number.parseInt(s, 16) >>> 0;
    const n = Number(s);
    if (Number.isFinite(n)) return n >>> 0;
  }
  throw new Error(`Invalid chainId value: ${String(x)}`);
}

/* ---------------------------------- Session ---------------------------------- */

const SESSION_KEY = 'animica.session.v1';

export type SavedSession = {
  address?: string;
  chainId?: number;
};

export function getSavedSession(): SavedSession | undefined {
  try {
    const raw = localStorage.getItem(SESSION_KEY);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === 'object') {
      const sess: SavedSession = {};
      if (typeof parsed.address === 'string') sess.address = parsed.address;
      if (typeof parsed.chainId === 'number') sess.chainId = parsed.chainId;
      return sess;
    }
  } catch { /* ignore */ }
  return;
}

export function saveSession(sess: SavedSession): void {
  try {
    localStorage.setItem(SESSION_KEY, JSON.stringify(sess));
  } catch { /* ignore */ }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(SESSION_KEY);
  } catch { /* ignore */ }
}

/* ---------------------------------- Utils ---------------------------------- */

function withTimeout<T>(p: Promise<T>, ms: number, onTimeoutError: Error): Promise<T> {
  if (!ms || ms <= 0 || !Number.isFinite(ms)) return p;
  let to: any;
  const t = new Promise<T>((_, rej) => {
    to = setTimeout(() => rej(onTimeoutError), ms);
  });
  return Promise.race([p, t]).finally(() => clearTimeout(to));
}

function timeoutError(method: string) {
  return new Error(`Provider request timed out: ${method}`);
}

function shapeProviderError(e: any, method: string): Error {
  // Normalize the common provider error shape to a standard Error.
  const msg = e?.message ?? String(e);
  const code = e?.code;
  const err = new Error(`Provider error (${method})${code ? ` [${code}]` : ''}: ${msg}`);
  (err as any).code = code;
  (err as any).data = e?.data;
  return err;
}
