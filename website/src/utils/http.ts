/**
 * HTTP utilities: fetch JSON with timeout & retry (browser/Node compatible).
 *
 * - AbortController-based timeouts (caller signal respected/merged)
 * - Exponential backoff with jitter for transient failures
 * - Sensible defaults for JSON APIs (Accept header, JSON parsing with fallback)
 */

export type HttpMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'HEAD' | 'OPTIONS';

export interface RetryPolicy {
  maxRetries?: number;        // default 3
  baseDelayMs?: number;       // default 300ms
  factor?: number;            // default 2
  maxDelayMs?: number;        // default 5000ms
  jitterRatio?: number;       // 0..1, default 0.3 (±30%)
  retryOnStatuses?: number[]; // default [429, 502, 503, 504]
}

export interface JsonRequestOptions extends Omit<RequestInit, 'body' | 'signal' | 'headers' | 'method'> {
  method?: HttpMethod;
  headers?: Record<string, string>;
  /** Merge into the query string (for GET/HEAD it is appended; for others it is appended as well). */
  query?: Record<string, string | number | boolean | null | undefined>;
  /** JSON payload; will be stringified and content-type set. */
  json?: unknown;
  /** Raw body; if provided, `json` is ignored. */
  body?: BodyInit | null;
  /** Max time per attempt (ms); default 10_000. 0/undefined disables timeouts. */
  timeoutMs?: number;
  /** Retry policy overrides. */
  retry?: RetryPolicy;
  /** External abort signal; merged with our timeout controller if provided. */
  signal?: AbortSignal;
}

export class HttpError<TBody = unknown> extends Error {
  name = 'HttpError';
  status: number;
  statusText: string;
  url: string;
  headers: Headers;
  body?: TBody;

  constructor(opts: { url: string; status: number; statusText: string; headers: Headers; body?: TBody; message?: string }) {
    super(opts.message ?? `HTTP ${opts.status} ${opts.statusText} (${opts.url})`);
    this.status = opts.status;
    this.statusText = opts.statusText;
    this.url = opts.url;
    this.headers = opts.headers;
    this.body = opts.body;
  }
}

/* --------------------------------- Public --------------------------------- */

export async function fetchJSON<T = unknown>(url: string, opts: JsonRequestOptions = {}): Promise<T> {
  const finalUrl = buildUrl(url, opts.query);
  const {
    method = (opts.json || opts.body) ? 'POST' : 'GET',
    headers = {},
    json,
    body,
    timeoutMs = 10_000,
    retry,
    signal,
    ...rest
  } = opts;

  const initHeaders: Record<string, string> = {
    Accept: headers['Accept'] ?? 'application/json',
    ...headers,
  };

  let requestBody: BodyInit | null | undefined = body ?? null;
  if (json !== undefined && body === undefined) {
    initHeaders['Content-Type'] = initHeaders['Content-Type'] ?? 'application/json';
    requestBody = JSON.stringify(json);
  }

  const attempt = async (attemptIndex: number, combinedSignal: AbortSignal) => {
    const res = await fetch(finalUrl, {
      ...rest,
      method,
      headers: initHeaders,
      body: requestBody,
      signal: combinedSignal,
    });

    if (!res.ok) {
      // Try to parse response body for richer error details
      const parsed = await parseMaybeJson(res);
      const err = new HttpError({
        url: finalUrl,
        status: res.status,
        statusText: res.statusText,
        headers: res.headers,
        body: parsed,
        message: errorMessageFromBody(res.status, res.statusText, parsed),
      });

      // Decide if we should retry on this status
      if (shouldRetryStatus(res.status, retry)) {
        throw wrapRetry(err); // signal to retry loop
      }
      throw err;
    }

    // Successful; parse JSON (or attempt to)
    return (await parseJsonOrThrow<T>(res, finalUrl)) as T;
  };

  return executeWithRetry<T>(
    attempt,
    {
      timeoutMs,
      externalSignal: signal,
      retry: normalizeRetry(retry),
    },
    finalUrl
  );
}

/** Convenience: GET JSON with query params. */
export function getJSON<T = unknown>(url: string, query?: JsonRequestOptions['query'], opts?: Omit<JsonRequestOptions, 'method' | 'query'>) {
  return fetchJSON<T>(url, { ...opts, method: 'GET', query });
}

/** Convenience: POST JSON payload and parse JSON response. */
export function postJSON<T = unknown>(url: string, json?: unknown, opts?: Omit<JsonRequestOptions, 'method' | 'json'>) {
  return fetchJSON<T>(url, { ...opts, method: 'POST', json });
}

/* --------------------------------- Internals ------------------------------- */

function buildUrl(url: string, query?: JsonRequestOptions['query']): string {
  if (!query || Object.keys(query).length === 0) return url;
  const u = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'http://localhost');
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null) continue;
    u.searchParams.set(k, String(v));
  }
  return u.toString();
}

function parseContentType(headers: Headers): string {
  const ct = headers.get('content-type') || '';
  return ct.split(';', 1)[0].trim().toLowerCase();
}

async function parseMaybeJson(res: Response): Promise<unknown> {
  const ct = parseContentType(res.headers);
  // Try JSON first if content-type indicates so
  if (ct === 'application/json' || ct === 'application/problem+json' || ct.endsWith('+json')) {
    try {
      return await res.clone().json();
    } catch {
      // fallthrough to text
    }
  }
  // Otherwise try text
  try {
    const txt = await res.clone().text();
    // Heuristically parse JSON-looking text
    if (txt && /^[\[{]/.test(txt.trim())) {
      try { return JSON.parse(txt); } catch { /* ignore */ }
    }
    return txt;
  } catch {
    return undefined;
  }
}

async function parseJsonOrThrow<T>(res: Response, urlForErr: string): Promise<T> {
  try {
    const data = await res.json();
    return data as T;
  } catch (e) {
    const text = await res.text().catch(() => '');
    throw new HttpError({
      url: urlForErr,
      status: res.status,
      statusText: res.statusText,
      headers: res.headers,
      body: text,
      message: `Expected JSON but got ${parseContentType(res.headers) || 'unknown'} (${urlForErr})`,
    });
  }
}

function errorMessageFromBody(status: number, statusText: string, body: unknown): string {
  if (body && typeof body === 'object') {
    // Common shapes: {error:{message}}, {message}, {detail}, problem+json fields, etc.
    const any = body as any;
    const msg = any?.error?.message ?? any?.message ?? any?.title ?? any?.detail;
    if (typeof msg === 'string' && msg.trim()) {
      return `HTTP ${status} ${statusText}: ${msg}`;
    }
  }
  if (typeof body === 'string' && body.trim()) {
    const snippet = body.trim().slice(0, 240).replace(/\s+/g, ' ');
    return `HTTP ${status} ${statusText}: ${snippet}`;
  }
  return `HTTP ${status} ${statusText}`;
}

function shouldRetryStatus(status: number, retry?: RetryPolicy): boolean {
  const list = retry?.retryOnStatuses ?? DEFAULT_RETRY.retryOnStatuses!;
  return list.includes(status);
}

const DEFAULT_RETRY: Required<RetryPolicy> = {
  maxRetries: 3,
  baseDelayMs: 300,
  factor: 2,
  maxDelayMs: 5000,
  jitterRatio: 0.3,
  retryOnStatuses: [429, 502, 503, 504],
};

function normalizeRetry(retry?: RetryPolicy): Required<RetryPolicy> {
  return {
    maxRetries: retry?.maxRetries ?? DEFAULT_RETRY.maxRetries,
    baseDelayMs: retry?.baseDelayMs ?? DEFAULT_RETRY.baseDelayMs,
    factor: retry?.factor ?? DEFAULT_RETRY.factor,
    maxDelayMs: retry?.maxDelayMs ?? DEFAULT_RETRY.maxDelayMs,
    jitterRatio: retry?.jitterRatio ?? DEFAULT_RETRY.jitterRatio,
    retryOnStatuses: retry?.retryOnStatuses ?? DEFAULT_RETRY.retryOnStatuses,
  };
}

function backoffDelayMs(attemptIndex: number, rp: Required<RetryPolicy>): number {
  const exp = Math.min(rp.maxDelayMs, rp.baseDelayMs * Math.pow(rp.factor, attemptIndex));
  const jitter = rp.jitterRatio * exp;
  // jitter in [-jitter, +jitter]
  const delta = (Math.random() * 2 - 1) * jitter;
  return Math.max(0, Math.floor(exp + delta));
}

function mergeSignals(ext?: AbortSignal, timeoutMs?: number): { signal: AbortSignal; cleanup: () => void } {
  // If no timeout and no external signal, return a dummy controller
  if (!ext && (!timeoutMs || timeoutMs <= 0)) {
    const c = new AbortController();
    return { signal: c.signal, cleanup: () => {} };
  }

  const controller = new AbortController();
  const onExternalAbort = () => {
    controller.abort((ext as any)?.reason ?? new DOMException('Aborted', 'AbortError'));
  };

  let timer: any;
  if (ext) {
    if (ext.aborted) {
      controller.abort((ext as any)?.reason ?? new DOMException('Aborted', 'AbortError'));
    } else {
      ext.addEventListener('abort', onExternalAbort, { once: true });
    }
  }
  if (timeoutMs && timeoutMs > 0) {
    timer = setTimeout(() => {
      controller.abort(new DOMException('Timeout', 'TimeoutError'));
    }, timeoutMs);
  }

  const cleanup = () => {
    if (ext) ext.removeEventListener('abort', onExternalAbort);
    if (timer) clearTimeout(timer);
  };

  return { signal: controller.signal, cleanup };
}

function wrapRetry(err: unknown): RetrySignalError {
  return new RetrySignalError(err);
}

class RetrySignalError extends Error {
  cause: unknown;
  constructor(cause: unknown) {
    super('retry');
    this.name = 'RetrySignalError';
    this.cause = cause;
  }
}

function isNetworkLikeError(e: unknown): boolean {
  // Fetch throws TypeError for network errors in browsers/Node
  if (e instanceof TypeError) return true;
  const name = (e as any)?.name;
  return name === 'AbortError' || name === 'TimeoutError';
}

async function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(signal.reason ?? new DOMException('Aborted', 'AbortError'));
    const t = setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(t);
      cleanup();
      reject(signal?.reason ?? new DOMException('Aborted', 'AbortError'));
    };
    const cleanup = () => {
      if (signal) signal.removeEventListener('abort', onAbort);
    };
    if (signal) signal.addEventListener('abort', onAbort, { once: true });
  });
}

async function executeWithRetry<T>(
  attempt: (attemptIndex: number, signal: AbortSignal) => Promise<T>,
  opts: { timeoutMs?: number; externalSignal?: AbortSignal; retry: Required<RetryPolicy> },
  urlForLog: string
): Promise<T> {
  const { retry } = opts;
  let lastError: any;

  for (let i = 0; i <= retry.maxRetries; i++) {
    const { signal, cleanup } = mergeSignals(opts.externalSignal, opts.timeoutMs);
    try {
      const result = await attempt(i, signal);
      cleanup();
      return result;
    } catch (e: any) {
      cleanup();

      // Decode HttpError vs network/timeouts
      const retriableStatus = e instanceof HttpError ? shouldRetryStatus(e.status, retry) : false;
      const retriableNetwork = isNetworkLikeError(e);
      const retriableWrapped = e instanceof RetrySignalError;

      if (i < retry.maxRetries && (retriableStatus || retriableNetwork || retriableWrapped)) {
        lastError = e instanceof RetrySignalError ? e.cause : e;
        const delay = backoffDelayMs(i, retry);
        // Wait with ability to abort externally
        await sleep(delay, opts.externalSignal).catch(() => {
          // If aborted during backoff, stop retrying
          throw (opts.externalSignal?.reason ?? new DOMException('Aborted', 'AbortError'));
        });
        continue;
      }

      // Not retriable or out of attempts — rethrow the innermost error
      throw (e instanceof RetrySignalError ? e.cause : e);
    }
  }

  // Exhausted (should not reach because loop throws on last attempt)
  throw lastError ?? new Error(`Request failed: ${urlForLog}`);
}

/* --------------------------------- Exports -------------------------------- */

export default {
  fetchJSON,
  getJSON,
  postJSON,
  HttpError,
};
