export interface CacheEntry<T> {
  value: T
  expiresAt: number
}

export class TtlCache {
  private store = new Map<string, CacheEntry<unknown>>()

  get<T>(key: string): T | undefined {
    const entry = this.store.get(key)
    if (!entry) return undefined
    if (Date.now() >= entry.expiresAt) {
      this.store.delete(key)
      return undefined
    }
    return entry.value as T
  }

  set<T>(key: string, value: T, ttlMs: number): void {
    this.store.set(key, { value, expiresAt: Date.now() + ttlMs })
  }

  clear(): void {
    this.store.clear()
  }
}

export class RequestCoalescer {
  private inFlight = new Map<string, Promise<unknown>>()

  async run<T>(key: string, fn: () => Promise<T>): Promise<T> {
    const existing = this.inFlight.get(key)
    if (existing) return existing as Promise<T>
    const promise = fn().finally(() => this.inFlight.delete(key))
    this.inFlight.set(key, promise)
    return promise
  }
}
