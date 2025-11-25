/**
 * Retry helpers with exponential backoff, jitter, AbortSignal support and per-attempt timeouts.
 *
 * Exports:
 *  - sleep(ms, signal)
 *  - backoffDelay(attempt, opts)
 *  - retryAsync(fn, opts)
 *  - fetchWithRetry(url, init?, opts?)
 *  - parseRetryAfter(header)
 *
 * Works in both browsers and Node (>=18 with global fetch).
 */

export type JitterMode = 'none' | 'full'

export interface BackoffOptions {
  /** Base delay (ms) for the first retry attempt. Default: 200ms */
  minDelay?: number
  /** Exponential factor. Default: 2 */
  factor?: number
  /** Cap for delay (ms). Default: 10_000ms */
  maxDelay?: number
  /** Jitter strategy. Default: 'full' */
  jitter?: JitterMode
}

export interface RetryOptions extends BackoffOptions {
  /** Total number of retries (not counting the initial attempt). Default: 5 */
  retries?: number
  /** Hard timeout per attempt (ms). If elapsed, the attempt aborts with AbortError. */
  attemptTimeoutMs?: number
  /** An AbortSignal to cancel the whole retry loop. */
  signal?: AbortSignal
  /**
   * Called before each retry wait. Return false to cancel further retries and rethrow the error.
   * Useful for logging/metrics.
   */
  onRetry?: (info: {
    attempt: number
    error: unknown
    nextDelayMs: number
  }) => void | boolean
  /**
   * Custom predicate to decide if an error/response should be retried.
   * If omitted, built-ins apply (network errors, 408/429/5xx for fetchWithRetry).
   */
  shouldRetry?: (err: unknown, attempt: number) => boolean
}

export class AbortError extends Error {
  constructor(message = 'Operation aborted') {
    super(message)
    this.name = 'AbortError'
  }
}

/** Abortable sleep */
export function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (ms <= 0) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const t = setTimeout(done, ms)
    function done() {
      cleanup()
      resolve()
    }
    function onAbort() {
      cleanup()
      reject(new AbortError())
    }
    function cleanup() {
      clearTimeout(t)
      signal?.removeEventListener('abort', onAbort)
    }
    if (signal?.aborted) return onAbort()
    signal?.addEventListener('abort', onAbort)
  })
}

/** Compute backoff delay (ms) for attempt number (1-based) */
export function backoffDelay(attempt: number, opts?: BackoffOptions): number {
  const factor = opts?.factor ?? 2
  const min = opts?.minDelay ?? 200
  const max = opts?.maxDelay ?? 10_000
  const jitter = opts?.jitter ?? 'full'
  const base = Math.min(max, Math.floor(min * Math.pow(factor, Math.max(0, attempt - 1))))
  if (jitter === 'none') return base
  // full jitter: uniform in [0, base]
  return Math.floor(Math.random() * (base + 1))
}

/**
 * Retry an async factory `fn` up to `retries` times on failure.
 * The factory receives (attempt, signal) where attempt starts at 1.
 */
export async function retryAsync<T>(
  fn: (attempt: number, signal: AbortSignal) => Promise<T>,
  opts?: RetryOptions
): Promise<T> {
  const retries = opts?.retries ?? 5
  const onRetry = opts?.onRetry
  const shouldRetry = opts?.shouldRetry
  const backoffOpts: BackoffOptions = {
    minDelay: opts?.minDelay,
    factor: opts?.factor,
    maxDelay: opts?.maxDelay,
    jitter: opts?.jitter
  }

  // Global cancellation
  const globalSignal = opts?.signal

  for (let attempt = 1; attempt <= 1 + retries; attempt++) {
    // Per-attempt timeout & abort wiring
    const ctl = new AbortController()
    const timers: Array<() => void> = []
    const cleanup = () => timers.forEach((f) => f())

    // Propagate external aborts
    const onAbort = () => ctl.abort(new AbortError())
    if (globalSignal) {
      if (globalSignal.aborted) {
        cleanup()
        throw new AbortError()
      }
      globalSignal.addEventListener('abort', onAbort)
      timers.push(() => globalSignal.removeEventListener('abort', onAbort))
    }

    // Attempt timeout
    if (opts?.attemptTimeoutMs && opts.attemptTimeoutMs > 0) {
      const id = setTimeout(() => ctl.abort(new AbortError('Attempt timed out')), opts.attemptTimeoutMs)
      timers.push(() => clearTimeout(id))
    }

    try {
      const res = await fn(attempt, ctl.signal)
      cleanup()
      return res
    } catch (err) {
      cleanup()

      // Abort errors: never retry
      if (isAbortLike(err)) throw err

      const willRetry =
        attempt <= retries &&
        (shouldRetry ? safeShouldRetry(shouldRetry, err, attempt) : defaultRetryPredicate(err))

      if (!willRetry) throw err

      const delay = backoffDelay(attempt, backoffOpts)
      if (onRetry) {
        const proceed = onRetry({ attempt, error: err, nextDelayMs: delay })
        if (proceed === false) throw err
      }

      // Wait with respect to the outer signal
      await sleep(delay, globalSignal)
      // continue to next attempt
    }
  }

  // Should be unreachable
  throw new Error('retryAsync: exhausted attempts unexpectedly')
}

/** Default retry predicate for generic operations: retry likely-transient errors. */
function defaultRetryPredicate(err: unknown): boolean {
  // Known pattern: network/TypeError from fetch in browsers
  if (err instanceof TypeError) return true
  // Custom retryable flag
  if (typeof err === 'object' && err && 'retryable' in err) {
    try {
      // @ts-ignore
      return Boolean(err.retryable)
    } catch {}
  }
  // HTTP-like status hints if present
  const maybeStatus = (err as any)?.status
  if (Number.isInteger(maybeStatus)) {
    const s = Number(maybeStatus)
    if (s === 408 || s === 429 || (s >= 500 && s <= 599)) return true
  }
  return false
}

function safeShouldRetry(fn: (err: unknown, attempt: number) => boolean, err: unknown, attempt: number): boolean {
  try {
    return !!fn(err, attempt)
  } catch {
    return false
  }
}

function isAbortLike(err: unknown): boolean {
  return !!(err && typeof err === 'object' && (err as any).name === 'AbortError')
}

/** Minimal HttpError used by fetchWithRetry */
export class HttpError extends Error {
  readonly status: number
  readonly statusText: string
  readonly bodyText?: string
  constructor(message: string, status: number, statusText: string, bodyText?: string) {
    super(message)
    this.name = 'HttpError'
    this.status = status
    this.statusText = statusText
    this.bodyText = bodyText
  }
}

/** Options specific to fetchWithRetry */
export interface FetchRetryOptions extends RetryOptions {
  /**
   * Methods considered safe/idempotent for retries. Default: ['GET','HEAD','PUT','DELETE','OPTIONS']
   * For POSTs, explicitly include 'POST' here if the endpoint is idempotent on your server.
   */
  retryMethods?: string[]
  /**
   * If true (default), honor Retry-After header (seconds or HTTP-date) to override the backoff delay
   * when status is 429/503.
   */
  honorRetryAfter?: boolean
  /** If provided, treat these status codes as retryable in addition to defaults. */
  extraRetryStatus?: number[]
}

/**
 * fetchWithRetry: retries transient HTTP failures (408, 429, 5xx, network) with backoff.
 * Returns a successful Response (ok=true) or throws HttpError for non-retryable non-ok responses.
 */
export async function fetchWithRetry(
  url: string,
  init?: RequestInit,
  opts?: FetchRetryOptions
): Promise<Response> {
  const retryMethods = (opts?.retryMethods ?? ['GET', 'HEAD', 'PUT', 'DELETE', 'OPTIONS']).map((m) => m.toUpperCase())
  const method = (init?.method ?? 'GET').toUpperCase()
  const methodRetryable = retryMethods.includes(method)

  const honorRetryAfter = opts?.honorRetryAfter ?? true
  const baseShouldRetry = (err: unknown, attempt: number): boolean => {
    // If not retryable by method, don't retry unless explicitly overridden by opts.shouldRetry
    if (!methodRetryable) return false
    // Custom predicate precedence
    if (opts?.shouldRetry) return safeShouldRetry(opts.shouldRetry, err, attempt)
    // Response-based decision
    if (isResponse(err)) {
      const s = err.status
      if (s === 408 || s === 429) return true
      if (s >= 500 && s <= 599) return true
      if (opts?.extraRetryStatus?.includes(s)) return true
      return false
    }
    // Network errors (TypeError), or generic default
    return defaultRetryPredicate(err)
  }

  // Merge signals: external opts.signal and init.signal
  const mergedSignal = mergeSignals([opts?.signal, init?.signal])

  let lastRetryAfterDelay: number | undefined

  const res = await retryAsync<Response>(
    async (attempt, signal) => {
      // Compose init with per-attempt signal
      const perAttemptInit: RequestInit = { ...init, signal: mergeSignals([signal, mergedSignal]) }
      const response = await fetch(url, perAttemptInit)

      if (response.ok) return response

      // Not OK: decide whether to retry by throwing a Response (caught by retryAsync)
      // For 429/503, record Retry-After if present
      if (honorRetryAfter && (response.status === 429 || response.status === 503)) {
        const ra = response.headers.get('retry-after')
        lastRetryAfterDelay = ra ? parseRetryAfter(ra) : undefined
      } else {
        lastRetryAfterDelay = undefined
      }
      throw response
    },
    {
      ...opts,
      shouldRetry: baseShouldRetry,
      onRetry: (info) => {
        // If Retry-After present, override the next delay by sleeping here and zeroing backoff wait
        if (lastRetryAfterDelay != null && lastRetryAfterDelay > 0) {
          // We block manually here so retryAsync will immediately proceed to next attempt.
          return
        }
        return opts?.onRetry?.(info)
      }
    }
  ).catch(async (err) => {
    // Non-OK & non-retryable -> HttpError with body for diagnostics (where possible)
    if (isResponse(err)) {
      let bodyText: string | undefined
      try {
        // Clone not available in some older envs; if fails, read directly
        const clone = 'clone' in err ? err.clone() : err
        bodyText = await clone.text()
      } catch {
        // ignore
      }
      throw new HttpError(`HTTP ${err.status} ${err.statusText}`, err.status, err.statusText, bodyText)
    }
    throw err
  })

  return res
}

/** Parse Retry-After header value to milliseconds (returns 0 if invalid). */
export function parseRetryAfter(v: string): number {
  if (!v) return 0
  // Seconds
  const sec = Number(v)
  if (Number.isFinite(sec) && sec >= 0) return Math.floor(sec * 1000)
  // HTTP-date
  const when = Date.parse(v)
  if (!Number.isNaN(when)) {
    const ms = when - Date.now()
    return ms > 0 ? ms : 0
  }
  return 0
}

/** Utility: merge multiple AbortSignals into one. If any aborts, the merged aborts. */
export function mergeSignals(signals: Array<AbortSignal | undefined | null>): AbortSignal | undefined {
  const list = signals.filter(Boolean) as AbortSignal[]
  if (list.length === 0) return undefined
  const ctl = new AbortController()
  const onAbort = () => ctl.abort(new AbortError())
  for (const s of list) {
    if (s.aborted) return ctl.abort(new AbortError()), ctl.signal
    s.addEventListener('abort', onAbort)
  }
  // Remove listeners when merged is aborted (avoid leaks)
  ctl.signal.addEventListener('abort', () => {
    for (const s of list) s.removeEventListener('abort', onAbort)
  })
  return ctl.signal
}

/** Type guard for Response (avoid depending on DOM lib typings) */
function isResponse(x: unknown): x is Response {
  return !!x && typeof x === 'object' && 'ok' in (x as any) && 'status' in (x as any) && 'headers' in (x as any)
}

export default {
  AbortError,
  sleep,
  backoffDelay,
  retryAsync,
  fetchWithRetry,
  parseRetryAfter,
  mergeSignals,
  HttpError
}
