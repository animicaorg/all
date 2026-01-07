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
