/**
 * Studio Services API (verify & artifacts)
 * -----------------------------------------------------------------------------
 * Production-ready, isomorphic client for the "studio-services" backend used by
 * Animica tools. This wrapper focuses on:
 *   - Contract verification (submit job, query by id/address/tx)
 *   - Artifacts (get by id, list per address, download blob)
 *
 * Features:
 *   • Robust fetch with timeouts, retries (exp backoff + jitter) on 429/5xx/network
 *   • API key auth via header or query param
 *   • TypeScript types for core responses
 *   • Works in browsers and Node (expects global fetch or user-provided one)
 */

export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [k: string]: JsonValue };

/* ----------------------------------- Types ----------------------------------- */

export interface Pagination<T> {
  items: T[];
  next?: string | null;
  prev?: string | null;
  total?: number;
}

export interface ArtifactMeta {
  id: string;
  kind: 'abi' | 'source' | 'bytecode' | 'manifest' | 'bundle' | string;
  size?: number;
  contentHash?: string; // hex
  createdAt?: string; // ISO8601
  // Optional direct content URL returned by the service (if static is enabled)
  href?: string;
  // Optional content type hints
  contentType?: string;
  // Arbitrary attributes (e.g., contract address, codeHash)
  attrs?: Record<string, JsonValue>;
}

export interface VerifyResult {
  verified: boolean;
  address: string;
  codeHash: string;
  abiId?: string | null;
  sourceId?: string | null;
  manifestId?: string | null;
  error?: string | null;
  verifiedAt?: string; // ISO8601
}

export type VerifyStatus = 'queued' | 'running' | 'settled' | 'failed';

export interface VerifyJob {
  id: string;
  status: VerifyStatus;
  result?: VerifyResult;
  submittedAt: string; // ISO8601
  updatedAt?: string;  // ISO8601
}

export interface VerifySubmitRequest {
  /** Contract address to verify */
  address: string;
  /** Compiler manifest (language, entry, ABI, metadata, etc.) */
  manifest: Record<string, JsonValue>;
  /**
   * Source files, keyed by path (relative). Values are UTF-8 strings.
   * If you need binary, base64-encode and set an appropriate flag in manifest.
   */
  source: Record<string, string>;
  /** Optional precomputed code hash to cross-check */
  codeHash?: string;
}

export interface VerifySubmitResponse {
  job: VerifyJob;
}

/* ---------------------------------- Options ---------------------------------- */

export interface ServicesApiOptions {
  /** Base URL to studio-services (e.g., https://services.example.com) */
  baseUrl: string;
  /** Optional path prefix for API (default: '') */
  apiPrefix?: string;
  /** API key if required by deployment */
  apiKey?: string;
  /** 'header' => Authorization: Bearer <key>, 'query' => ?key= (default: header) */
  authScheme?: 'header' | 'query';
  /** Extra headers always sent */
  headers?: Record<string, string>;
  /** GET retries (default: 3) */
  retries?: number;
  /** POST retries (default: 1) */
  postRetries?: number;
  /** Base backoff in ms (default: 250) */
  backoffBaseMs?: number;
  /** Max backoff in ms (default: 2500) */
  backoffMaxMs?: number;
  /** Timeout per request in ms (default: 12_000) */
  timeoutMs?: number;
  /** Custom fetch, otherwise uses global fetch */
  fetch?: typeof fetch;
}

/* --------------------------------- Utilities --------------------------------- */

function trimRightSlash(s: string): string {
  return s.replace(/\/+$/, '');
}
function joinUrl(base: string, path: string): string {
  if (!path.startsWith('/')) path = '/' + path;
  return trimRightSlash(base) + path;
}
function jitter(ms: number): number {
  return Math.floor(Math.random() * ms);
}
function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
function toQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return '';
  const ent = Object.entries(params).filter(([, v]) => v !== undefined && v !== null);
  if (!ent.length) return '';
  return (
    '?' +
    ent
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
      .join('&')
  );
}

/* ----------------------------------- Client ---------------------------------- */

export class ServicesApi {
  private base: string;
  private prefix: string;
  private key?: string;
  private authScheme: 'header' | 'query';
  private headers: Record<string, string>;
  private retries: number;
  private postRetries: number;
  private backoffBaseMs: number;
  private backoffMaxMs: number;
  private timeoutMs: number;
  private _fetch: typeof fetch;

  constructor(opts: ServicesApiOptions) {
    this.base = trimRightSlash(opts.baseUrl);
    this.prefix = opts.apiPrefix ?? '';
    this.key = opts.apiKey;
    this.authScheme = opts.authScheme ?? 'header';
    this.headers = {
      Accept: 'application/json',
      ...(opts.headers ?? {}),
    };
    this.retries = opts.retries ?? 3;
    this.postRetries = opts.postRetries ?? 1;
    this.backoffBaseMs = opts.backoffBaseMs ?? 250;
    this.backoffMaxMs = opts.backoffMaxMs ?? 2500;
    this.timeoutMs = opts.timeoutMs ?? 12_000;
    this._fetch = opts.fetch ?? (globalThis.fetch as any);
    if (!this._fetch) throw new Error('fetch is not available in this environment');
  }

  /* ------------------------------ Core request ------------------------------ */

  private authHeaders(): Record<string, string> {
    if (this.authScheme === 'header' && this.key) {
      return { Authorization: `Bearer ${this.key}` };
    }
    return {};
  }

  private url(path: string, query?: Record<string, string | number | boolean | undefined | null>): string {
    const withPrefix = this.prefix ? joinUrl(this.base, this.prefix + path) : joinUrl(this.base, path);
    const url =
      this.authScheme === 'query' && this.key
        ? withPrefix + toQuery({ ...(query ?? {}), key: this.key })
        : withPrefix + toQuery(query ?? {});
    return url;
  }

  private async doFetch<T>(
    method: 'GET' | 'POST',
    path: string,
    opts?: {
      query?: Record<string, string | number | boolean | undefined | null>;
      json?: any;
      formData?: FormData;
      retriesOverride?: number;
      extraHeaders?: Record<string, string>;
      expectJson?: boolean;
      asArrayBuffer?: boolean;
    }
  ): Promise<T> {
    const {
      query,
      json,
      formData,
      retriesOverride,
      extraHeaders,
      expectJson = true,
      asArrayBuffer = false,
    } = opts ?? {};

    const url = this.url(path, query);
    const hdrs: Record<string, string> = {
      ...this.headers,
      ...this.authHeaders(),
      ...(extraHeaders ?? {}),
    };

    let body: BodyInit | undefined;
    if (method === 'POST') {
      if (formData) {
        body = formData as any;
        // let browser set multipart boundary
      } else {
        hdrs['Content-Type'] = 'application/json';
        body = JSON.stringify(json ?? {});
      }
    }

    let attempt = 0;
    const maxAttempts =
      retriesOverride ?? (method === 'GET' ? this.retries : this.postRetries);

    let lastErr: any;

    while (attempt <= maxAttempts) {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), this.timeoutMs);

      try {
        const res = await this._fetch(url, {
          method,
          headers: hdrs,
          body,
          signal: controller.signal,
        });
        clearTimeout(t);

        // Retry on 429/5xx
        if (res.status === 429 || (res.status >= 500 && res.status <= 599)) {
          lastErr = new Error(`HTTP ${res.status}`);
        } else if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new Error(`HTTP ${res.status}: ${text || res.statusText}`);
        } else {
          if (asArrayBuffer) {
            const buf = await res.arrayBuffer();
            return buf as unknown as T;
          }
          if (!expectJson) {
            // @ts-ignore
            return (await res.text()) as T;
          }
          const data = (await res.json()) as T;
          return data;
        }
      } catch (e: any) {
        lastErr = e;
      } finally {
        clearTimeout(t);
      }

      attempt++;
      if (attempt > maxAttempts) break;
      const exp = Math.min(this.backoffMaxMs, this.backoffBaseMs * 2 ** (attempt - 1));
      await sleep(exp + jitter(exp));
    }

    throw lastErr ?? new Error('Request failed');
  }

  private get<T>(
    path: string,
    query?: Record<string, string | number | boolean | undefined | null>,
    expectJson = true,
    asArrayBuffer = false
  ) {
    return this.doFetch<T>('GET', path, { query, expectJson, asArrayBuffer });
  }
  private post<T>(
    path: string,
    json?: any,
    query?: Record<string, string | number | boolean | undefined | null>,
    formData?: FormData
  ) {
    return this.doFetch<T>('POST', path, { json, query, formData });
  }

  /* -------------------------------- Verify ---------------------------------- */

  /** POST /verify — submit a verification job */
  submitVerify(payload: VerifySubmitRequest): Promise<VerifySubmitResponse> {
    return this.post('/verify', payload);
  }

  /** GET /verify/{jobId} — fetch job status by id (preferred if supported) */
  getVerifyStatus(jobId: string): Promise<VerifyJob> {
    // Primary: /verify/status/{id}; Fallback: /verify/{id}
    return this.get<VerifyJob>(`/verify/status/${encodeURIComponent(jobId)}`).catch(() =>
      this.get<VerifyJob>(`/verify/${encodeURIComponent(jobId)}`)
    );
  }

  /** GET /verify/address/{addr} — latest verification result for an address */
  async getVerifyByAddress(addr: string): Promise<VerifyResult | VerifyJob> {
    const encoded = encodeURIComponent(addr);
    // Preferred explicit path:
    try {
      return await this.get<VerifyResult>(`/verify/address/${encoded}`);
    } catch {
      // Fallback to ambiguous legacy path if server uses /verify/{address}
      return this.get<VerifyResult | VerifyJob>(`/verify/${encoded}`);
    }
  }

  /** GET /verify/tx/{txHash} — verification info tied to a tx (if available) */
  async getVerifyByTx(txHash: string): Promise<VerifyResult | VerifyJob> {
    const encoded = encodeURIComponent(txHash);
    // Preferred explicit path:
    try {
      return await this.get<VerifyResult>(`/verify/tx/${encoded}`);
    } catch {
      // Fallback to ambiguous legacy path
      return this.get<VerifyResult | VerifyJob>(`/verify/${encoded}`);
    }
  }

  /* ------------------------------- Artifacts -------------------------------- */

  /** GET /artifacts/{id} — fetch artifact metadata */
  getArtifactMeta(id: string): Promise<ArtifactMeta> {
    return this.get(`/artifacts/${encodeURIComponent(id)}`);
  }

  /** GET /address/{addr}/artifacts — list artifacts linked to an address */
  listArtifactsByAddress(
    addr: string,
    params?: { offset?: number; limit?: number }
  ): Promise<Pagination<ArtifactMeta>> {
    return this.get(`/address/${encodeURIComponent(addr)}/artifacts`, params);
  }

  /**
   * Resolve a direct content URL for the artifact if the backend exposes one.
   * Falls back to a conventional /artifacts/{id}/blob route.
   */
  getArtifactContentUrl(id: string, meta?: ArtifactMeta): string {
    if (meta?.href) {
      return meta.href.startsWith('http')
        ? meta.href
        : this.url(meta.href.startsWith('/') ? meta.href : '/' + meta.href);
    }
    // Default blob path convention:
    return this.url(`/artifacts/${encodeURIComponent(id)}/blob`);
  }

  /**
   * Download raw artifact bytes (ArrayBuffer). Useful for ABI JSON, bundles, etc.
   * Tries meta.href (if provided), otherwise uses /artifacts/{id}/blob.
   */
  async downloadArtifact(id: string): Promise<Uint8Array> {
    let meta: ArtifactMeta | undefined;
    try {
      meta = await this.getArtifactMeta(id);
    } catch {
      // meta might be protected; try direct blob anyway
    }
    const urlPath = meta?.href
      ? // If href is absolute, synthesize a full URL; else let GET handle it
        (meta.href.startsWith('http')
          ? meta.href
          : this.url(meta.href.startsWith('/') ? meta.href : '/' + meta.href))
      : this.url(`/artifacts/${encodeURIComponent(id)}/blob`);

    // We need to go through doFetch to keep auth/timeout/retries.
    const buf = await this.doFetch<ArrayBuffer>('GET', urlPath.replace(this.base, ''), {
      // When urlPath is absolute, replace(...) won't strip base. In that case, we pass the full URL by
      // abusing "path" and letting doFetch join incorrectly. To avoid that, call fetch directly here:
      expectJson: false,
      asArrayBuffer: true,
      // Use a single attempt for binary to avoid long stalls on large files
      retriesOverride: 0,
    }).catch(async () => {
      // Absolute URL fallback: fetch directly with headers/timeouts (no retries)
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), this.timeoutMs);
      try {
        const res = await this._fetch(urlPath, {
          method: 'GET',
          headers: { ...this.headers, ...this.authHeaders() },
          signal: controller.signal,
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.arrayBuffer();
      } finally {
        clearTimeout(t);
      }
    });

    return new Uint8Array(buf);
  }
}

/* --------------------------------- Helpers ---------------------------------- */

/**
 * Construct a ServicesApi from env-like values (Vite-compatible).
 * Uses:
 *  - VITE_SERVICES_URL (base URL)
 *  - VITE_SERVICES_KEY (optional API key)
 *  - VITE_SERVICES_PREFIX (optional path prefix, e.g. '/api')
 */
export function servicesApiFromEnv(env: {
  VITE_SERVICES_URL?: string;
  VITE_SERVICES_KEY?: string;
  VITE_SERVICES_PREFIX?: string;
}): ServicesApi {
  // Prefer explicit env param; fall back to import.meta.env in Vite
  const base =
    env.VITE_SERVICES_URL ||
    (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_SERVICES_URL);
  if (!base) throw new Error('VITE_SERVICES_URL is required to use ServicesApi');

  const key =
    env.VITE_SERVICES_KEY ||
    (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_SERVICES_KEY) ||
    undefined;

  const prefix =
    env.VITE_SERVICES_PREFIX ||
    (typeof import.meta !== 'undefined' && (import.meta as any).env?.VITE_SERVICES_PREFIX) ||
    '';

  return new ServicesApi({
    baseUrl: base,
    apiPrefix: prefix,
    apiKey: key,
    authScheme: 'header',
  });
}
