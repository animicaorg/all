/**
 * Shared HTTP plumbing: injectable fetch + timeout handling.
 * Zero runtime dependencies — relies on the WHATWG fetch built into Node >= 18.
 */

export type FetchLike = (
  input: string | URL,
  init?: RequestInit
) => Promise<Response>;

export interface HttpOptions {
  /** Custom fetch implementation (for testing or polyfills). Defaults to globalThis.fetch. */
  fetch?: FetchLike;
  /** Per-request timeout in milliseconds. Default: 30000. Set 0 to disable. */
  timeoutMs?: number;
  /** Extra headers merged into every request. */
  headers?: Record<string, string>;
}

export class HttpError extends Error {
  readonly status: number;
  readonly url: string;
  readonly body?: string;

  constructor(status: number, url: string, body?: string) {
    super(`HTTP ${status} from ${url}`);
    this.name = "HttpError";
    this.status = status;
    this.url = url;
    this.body = body;
  }
}

export function resolveFetch(fetchImpl?: FetchLike): FetchLike {
  const f = fetchImpl ?? (globalThis.fetch as FetchLike | undefined);
  if (typeof f !== "function") {
    throw new Error(
      "No fetch implementation available. Use Node >= 18 or pass { fetch } explicitly."
    );
  }
  return f;
}

/** Run fetch with an AbortController-based timeout (no-op when timeoutMs is 0). */
export async function fetchWithTimeout(
  fetchImpl: FetchLike,
  url: string | URL,
  init: RequestInit,
  timeoutMs: number
): Promise<Response> {
  if (!timeoutMs || timeoutMs <= 0) {
    return fetchImpl(url, init);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetchImpl(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

/** GET a JSON document, throwing HttpError on non-2xx. */
export async function getJson<T>(
  url: string,
  opts: HttpOptions = {}
): Promise<T> {
  const f = resolveFetch(opts.fetch);
  const res = await fetchWithTimeout(
    f,
    url,
    {
      method: "GET",
      headers: { accept: "application/json", ...(opts.headers ?? {}) },
    },
    opts.timeoutMs ?? 30_000
  );
  if (!res.ok) {
    let body: string | undefined;
    try {
      body = await res.text();
    } catch {
      /* ignore */
    }
    throw new HttpError(res.status, url, body);
  }
  return (await res.json()) as T;
}
