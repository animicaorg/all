import fs from 'node:fs'
import { dirname } from 'node:path'

export interface CacheEntry<T> {
  value: T
  expiresAt: number
}

export class TtlCache {
  private store = new Map<string, CacheEntry<unknown>>()
  private persistPath?: string
  private flushTimer: NodeJS.Timeout | null = null

  constructor(options?: { persistPath?: string }) {
    this.persistPath = options?.persistPath
    if (this.persistPath) {
      this.loadFromDisk(this.persistPath)
    }
  }

  get<T>(key: string): T | undefined {
    const entry = this.store.get(key)
    if (!entry) return undefined
    if (Date.now() >= entry.expiresAt) {
      this.store.delete(key)
      this.scheduleFlush()
      return undefined
    }
    return entry.value as T
  }

  set<T>(key: string, value: T, ttlMs: number): void {
    this.store.set(key, { value, expiresAt: Date.now() + ttlMs })
    this.scheduleFlush()
  }

  clear(): void {
    this.store.clear()
    this.scheduleFlush()
  }

  private loadFromDisk(filePath: string): void {
    try {
      if (!fs.existsSync(filePath)) return
      const payload = JSON.parse(fs.readFileSync(filePath, 'utf-8')) as { entries?: Record<string, CacheEntry<unknown>> }
      if (!payload?.entries) return
      const now = Date.now()
      for (const [key, entry] of Object.entries(payload.entries)) {
        if (!entry || typeof entry.expiresAt !== 'number' || entry.expiresAt <= now) continue
        this.store.set(key, entry)
      }
    } catch {
      // Ignore cache load failures; fallback to empty cache.
    }
  }

  private scheduleFlush(): void {
    if (!this.persistPath) return
    if (this.flushTimer) return
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null
      this.flushToDisk()
    }, 200)
  }

  private flushToDisk(): void {
    if (!this.persistPath) return
    const now = Date.now()
    const entries = Object.fromEntries(
      [...this.store.entries()].filter(([, entry]) => entry.expiresAt > now)
    )
    try {
      fs.mkdirSync(dirname(this.persistPath), { recursive: true })
      fs.writeFileSync(this.persistPath, JSON.stringify({ entries }, jsonReplacer))
    } catch {
      // Ignore cache persistence failures; cache will stay in memory.
    }
  }
}

function jsonReplacer(_key: string, value: unknown): unknown {
  if (typeof value === 'bigint') return value.toString()
  if (value instanceof Uint8Array) return Buffer.from(value).toString('hex')
  return value
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
