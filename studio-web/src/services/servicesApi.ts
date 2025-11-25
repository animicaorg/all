/**
 * studio-web services API client for studio-services:
 * - Deploy (relay signed CBOR tx)
 * - Verify (submit source/manifest; poll status)
 * - Faucet (optional)
 * - Simulate (compile+execute without state writes)
 *
 * Reads defaults from:
 *  - VITE_SERVICES_URL (e.g. http://localhost:8787)
 *  - VITE_SERVICES_API_KEY (optional; sent as Bearer)
 */

export type ServicesConfig = {
  baseUrl: string;               // no trailing slash, e.g. http://localhost:8787
  apiKey?: string;               // optional API key (sent as Authorization: Bearer <key>)
  /** request timeout in ms (per attempt) */
  timeoutMs?: number;
  /** number of retries on 429/5xx/network errors */
  retries?: number;
  /** base delay for exponential backoff (ms) */
  retryBaseDelayMs?: number;
};

export type PartialServicesConfig = Partial<ServicesConfig>;

/* --------------------------------- Deploy --------------------------------- */

export type DeployRequest = {
  /** Signed CBOR transaction, hex string with or without 0x prefix */
  rawTx: string;
};

export type DeployResponse = {
  txHash: string; // 0x…
};

/** Optional preflight check on the same signed tx (no state write) */
export type PreflightRequest = {
  rawTx: string;
};

export type PreflightResponse = {
  ok: boolean;
  gasUsed?: number;
  error?: string;
};

/* --------------------------------- Verify --------------------------------- */

export type VerifyRequest = {
  /** Contract Python source as UTF-8 string */
  source: string;
  /** Manifest JSON (ABI, metadata) */
  manifest: Record<string, unknown>;
  /**
   * Contract address to bind verification result to (bech32m anim1…).
   * Provide either `address` or `txHash` (or both).
   */
  address?: string;
  /** Deploy transaction hash, if verifying by tx linkage */
  txHash?: string;
};

export type VerifyQueued = { status: 'queued'; jobId: string };
export type VerifyOk = {
  status: 'ok';
  address: string;
  codeHash: string; // 0x…
  manifestHash: string; // 0x…
  verifiedAt: string; // ISO timestamp
};
export type VerifyFail = { status: 'failed'; error: string };
export type VerifyStatus = VerifyQueued | VerifyOk | VerifyFail;

/* --------------------------------- Faucet --------------------------------- */

export type FaucetRequest = {
  address: string;     // bech32m anim1…
  /** amount as decimal string in minimal unit (optional; service may bound it) */
  amount?: string;
};

export type FaucetResponse = {
  granted: string;     // decimal string actually granted
  txHash?: string;     // if service relays a funding tx
};

/* -------------------------------- Simulate -------------------------------- */

export type SimCall = {
  /** Function name to call (e.g., "inc" or "get") */
  fn: string;
  /** Positional arguments (ABI-encoded types are inferred by service) */
  args?: unknown[];
};

export type SimulateRequest = {
  source: string;                          // contract.py
  manifest: Record<string, unknown>;       // ABI + metadata
  call: SimCall;                            // which function to invoke
  /** Optional deterministic seed (hex or decimal) for local PRNG */
  seed?: string | number;
};

export type SimulateResult = {
  ok: boolean;
  return?: unknown;
  logs?: { name: string; args: Record<string, unknown> }[];
  gasUsed?: number;
  error?: string;
};

/* ------------------------------- Artifacts (optional) ------------------------------ */

export type ArtifactPut = {
  address: string; // anim1…
  manifest: Record<string, unknown>;
  abi?: Record<string, unknown>;
  codeHash?: string; // 0x…
};
export type ArtifactMeta = {
  id: string;
  address: string;
  codeHash: string;
  manifestHash: string;
  createdAt: string;
};

/* --------------------------------- Client --------------------------------- */

function envString(name: string, fallback?: string): string | undefined {
  // @ts-expect-error vite typing at runtime
  const v = (import.meta as any)?.env?.[name];
  return v ?? fallback;
}

export function getDefaultServicesConfig(overrides: PartialServicesConfig = {}): ServicesConfig {
  const baseUrl =
    overrides.baseUrl ??
    envString('VITE_SERVICES_URL') ??
    'http://127.0.0.1:8787';
  const apiKey =
    overrides.apiKey ??
    envString('VITE_SERVICES_API_KEY');

  return {
    baseUrl: stripTrailingSlash(baseUrl),
    apiKey,
    timeoutMs: overrides.timeoutMs ?? 12_000,
    retries: overrides.retries ?? 2,
    retryBaseDelayMs: overrides.retryBaseDelayMs ?? 300,
  };
}

export class ServicesApi {
  private cfg: ServicesConfig;

  constructor(cfg: PartialServicesConfig = {}) {
    this.cfg = getDefaultServicesConfig(cfg);
  }

  /* --------------------------------- Deploy --------------------------------- */

  async deploy(req: DeployRequest): Promise<DeployResponse> {
    const body = { rawTx: to0x(req.rawTx) };
    return this._postJson('/deploy', body);
  }

  async preflight(req: PreflightRequest): Promise<PreflightResponse> {
    const body = { rawTx: to0x(req.rawTx) };
    return this._postJson('/preflight', body);
  }

  /* --------------------------------- Verify --------------------------------- */

  async verify(req: VerifyRequest): Promise<VerifyStatus> {
    return this._postJson('/verify', req);
  }

  async getVerifyByAddress(address: string): Promise<VerifyStatus> {
    return this._getJson(`/verify/${encodeURIComponent(address)}`);
  }

  async getVerifyByTxHash(txHash: string): Promise<VerifyStatus> {
    return this._getJson(`/verify/${encodeURIComponent(txHash)}`);
  }

  /* --------------------------------- Faucet --------------------------------- */

  async faucetDrip(req: FaucetRequest): Promise<FaucetResponse> {
    return this._postJson('/faucet/drip', req);
  }

  /* -------------------------------- Simulate -------------------------------- */

  async simulate(req: SimulateRequest): Promise<SimulateResult> {
    return this._postJson('/simulate', req);
  }

  /* -------------------------------- Artifacts -------------------------------- */

  async putArtifact(artifact: ArtifactPut): Promise<ArtifactMeta> {
    return this._postJson('/artifacts', artifact);
  }

  async getArtifact(id: string): Promise<ArtifactMeta> {
    return this._getJson(`/artifacts/${encodeURIComponent(id)}`);
  }

  async listArtifactsByAddress(address: string): Promise<ArtifactMeta[]> {
    return this._getJson(`/address/${encodeURIComponent(address)}/artifacts`);
  }

  /* --------------------------------- Health -------------------------------- */

  async health(): Promise<{ status: 'ok' } | Record<string, unknown>> {
    // service returns JSON; if text, coerce
    return this._getJson('/healthz').catch(async (e) => {
      // fall back to plain text GET
      const resp = await this._request('/healthz', { method: 'GET' });
      if (resp.ok) return { status: 'ok' as const };
      throw e;
    });
  }

  /* ------------------------------- HTTP helpers ------------------------------ */

  private async _getJson<T>(path: string): Promise<T> {
    const resp = await this._request(path, {
      method: 'GET',
      headers: { Accept: 'application/json' },
    });
    return this._handleJson<T>(resp);
  }

  private async _postJson<T>(path: string, json: unknown): Promise<T> {
    const resp = await this._request(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(json),
    });
    return this._handleJson<T>(resp);
  }

  private async _handleJson<T>(resp: Response): Promise<T> {
    const text = await resp.text();
    const parse = () => (text ? JSON.parse(text) : null);
    if (resp.ok) {
      return (text ? parse() : ({} as any)) as T;
    }
    let detail: any = null;
    try { detail = parse(); } catch { /* ignore parse error */ }
    const message = detail?.error || detail?.detail || detail?.message || resp.statusText || 'Request failed';
    const error: any = new Error(message);
    error.status = resp.status;
    error.body = detail ?? text;
    throw error;
  }

  private async _request(path: string, init: RequestInit): Promise<Response> {
    const url = this.cfg.baseUrl + normalizePath(path);
    const headers: Record<string, string> = { ...(init.headers as any) };
    if (this.cfg.apiKey) {
      headers['Authorization'] = `Bearer ${this.cfg.apiKey}`;
    }

    const attempt = async (signal: AbortSignal): Promise<Response> => {
      const reqInit: RequestInit = { ...init, headers, signal };
      return fetch(url, reqInit);
    };

    const { retries, timeoutMs, retryBaseDelayMs } = this.cfg;
    let lastErr: any;
    for (let i = 0; i <= (retries ?? 0); i++) {
      const controller = new AbortController();
      const t = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const resp = await attempt(controller.signal);
        clearTimeout(t);
        if (i < (retries ?? 0) && shouldRetry(resp.status)) {
          await sleep(jitterBackoff(i, retryBaseDelayMs));
          continue;
        }
        return resp;
      } catch (e: any) {
        clearTimeout(t);
        lastErr = e;
        // network/timeout: retry if attempts remain
        if (i < (retries ?? 0)) {
          await sleep(jitterBackoff(i, retryBaseDelayMs));
          continue;
        }
        throw e;
      }
    }
    throw lastErr ?? new Error('Unknown request error');
  }
}

/* --------------------------------- Helpers -------------------------------- */

function stripTrailingSlash(u: string): string {
  return u.endsWith('/') ? u.slice(0, -1) : u;
}

function normalizePath(p: string): string {
  if (!p.startsWith('/')) return '/' + p;
  return p;
}

function to0x(hexLike: string): string {
  const s = hexLike.trim();
  if (s.startsWith('0x') || s.startsWith('0X')) return '0x' + s.slice(2);
  if (/^[0-9a-fA-F]+$/.test(s)) return '0x' + s;
  throw new Error('Expected hex string for rawTx');
}

function shouldRetry(status: number): boolean {
  // 429 Too Many Requests + 5xx server errors
  return status === 429 || (status >= 500 && status < 600);
}

function jitterBackoff(attempt: number, baseMs = 300): number {
  const exp = Math.pow(2, attempt);
  const max = baseMs * exp;
  return Math.floor(max * (0.5 + Math.random() * 0.5));
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/* ------------------------------- Singleton -------------------------------- */

let _services: ServicesApi | null = null;

/**
 * A shared, lazily-initialized instance for app-wide calls.
 * Pass overrides to bypass or create a custom-scoped client.
 */
export function getServices(overrides?: PartialServicesConfig): ServicesApi {
  if (overrides) return new ServicesApi(overrides);
  if (!_services) _services = new ServicesApi();
  return _services;
}

export default getServices;
