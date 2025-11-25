/**
 * Typed event streams used by the in-page provider (window.animica).
 * These are fed by background → content → page messages and surfaced
 * to dapps via provider.on(event, handler).
 */

export type Address = string;

export type NewHead = {
  number: number;
  hash: string;
  parentHash: string;
  timestamp: number; // seconds since epoch
};

export type ChainChanged = {
  chainId: string; // hex or decimal string, e.g. "0x1" or "1"
  networkName?: string;
};

export type AccountsChanged = Address[];

/** Minimal strongly-typed stream with lastValue memory. */
export class Stream<T> {
  private listeners = new Set<(value: T) => void>();
  private lastValue?: T;

  /** Subscribe; returns an unsubscribe function. If `emitLast` and a value exists, it's delivered immediately. */
  on(listener: (value: T) => void, emitLast = false): () => void {
    this.listeners.add(listener);
    if (emitLast && this.lastValue !== undefined) {
      // deliver asynchronously to avoid re-entrancy hazards
      queueMicrotask(() => {
        try {
          listener(this.lastValue as T);
        } catch {
          /* ignore */
        }
      });
    }
    return () => this.off(listener);
  }

  off(listener: (value: T) => void): void {
    this.listeners.delete(listener);
  }

  once(listener: (value: T) => void, emitLastIfAny = true): () => void {
    const wrapped = (v: T) => {
      try {
        listener(v);
      } finally {
        this.off(wrapped);
      }
    };
    const unsub = this.on(wrapped);
    if (emitLastIfAny && this.lastValue !== undefined) {
      queueMicrotask(() => wrapped(this.lastValue as T));
    }
    return unsub;
  }

  /** Emit to subscribers and remember the last value. */
  emit(value: T): void {
    this.lastValue = value;
    if (this.listeners.size === 0) return;
    // Snapshot to avoid mutation during iteration
    const ls = Array.from(this.listeners);
    for (const fn of ls) {
      try {
        fn(value);
      } catch {
        // Listener errors must not break others
      }
    }
  }

  /** Read the last emitted value, if any. */
  getLast(): T | undefined {
    return this.lastValue;
  }

  /** Remove all listeners (used on teardown / disconnect). */
  clear(): void {
    this.listeners.clear();
  }
}

/** Container for all provider-facing streams. Singleton exported below. */
export class ProviderStreams {
  readonly accountsChanged = new Stream<AccountsChanged>();
  readonly chainChanged = new Stream<ChainChanged>();
  readonly newHeads = new Stream<NewHead>();

  /** Helper to reset listeners on hard reload/disconnect. */
  reset(): void {
    this.accountsChanged.clear();
    this.chainChanged.clear();
    this.newHeads.clear();
  }

  /** Apply a normalized incoming event (from background/content bridge). */
  applyIncoming(evt: IncomingEvent): void {
    switch (evt.type) {
      case "accountsChanged":
        this.accountsChanged.emit(evt.accounts);
        break;
      case "chainChanged":
        this.chainChanged.emit({ chainId: evt.chainId, networkName: evt.networkName });
        break;
      case "newHeads":
        this.newHeads.emit(evt.head);
        break;
    }
  }
}

/** Shape of events expected from the background/content bridge. */
export type IncomingEvent =
  | { type: "accountsChanged"; accounts: AccountsChanged }
  | { type: "chainChanged"; chainId: string; networkName?: string }
  | { type: "newHeads"; head: NewHead };

/** Singleton used by provider/index.ts */
export const streams = new ProviderStreams();

/* ----------------------------- EventEmitter shim ---------------------------- */
/**
 * Small adapter so provider can expose a familiar .on/.removeListener API per AIP-1193 style:
 *
 *  provider.on('accountsChanged', handler)
 *  provider.on('chainChanged', handler)
 *  provider.on('newHeads', handler)
 */
export type ProviderEventName = "accountsChanged" | "chainChanged" | "newHeads";
export type ProviderEventHandlerMap = {
  accountsChanged: (accounts: AccountsChanged) => void;
  chainChanged: (info: ChainChanged) => void;
  newHeads: (head: NewHead) => void;
};

export function addProviderListener<E extends ProviderEventName>(
  event: E,
  handler: ProviderEventHandlerMap[E]
): () => void {
  switch (event) {
    case "accountsChanged":
      return streams.accountsChanged.on(handler as ProviderEventHandlerMap["accountsChanged"]);
    case "chainChanged":
      return streams.chainChanged.on(handler as ProviderEventHandlerMap["chainChanged"]);
    case "newHeads":
      return streams.newHeads.on(handler as ProviderEventHandlerMap["newHeads"]);
  }
}

/** Remove a specific listener previously passed to addProviderListener */
export function removeProviderListener<E extends ProviderEventName>(
  event: E,
  handler: ProviderEventHandlerMap[E]
): void {
  switch (event) {
    case "accountsChanged":
      streams.accountsChanged.off(handler as ProviderEventHandlerMap["accountsChanged"]);
      break;
    case "chainChanged":
      streams.chainChanged.off(handler as ProviderEventHandlerMap["chainChanged"]);
      break;
    case "newHeads":
      streams.newHeads.off(handler as ProviderEventHandlerMap["newHeads"]);
      break;
  }
}

/** Emit helper for provider internals (avoid exposing Stream instances) */
export const emit = {
  accountsChanged: (accounts: AccountsChanged) => streams.accountsChanged.emit(accounts),
  chainChanged: (info: ChainChanged) => streams.chainChanged.emit(info),
  newHeads: (head: NewHead) => streams.newHeads.emit(head),
};
