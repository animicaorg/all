/**
 * Data Availability (DA) convenience helpers for Studio Web.
 *
 * These helpers talk directly to the node's DA retrieval service, which is
 * typically mounted by the node RPC under the same base URL:
 *   - POST   {RPC}/da/blob                 -> { commitment: 0x..., receipt?: {...} }
 *   - GET    {RPC}/da/blob/{commitment}    -> raw blob bytes
 *   - GET    {RPC}/da/proof?commitment=..  -> { proof: {...}, samples: [...], root: 0x... }
 *
 * Configuration:
 *   - Base RPC URL is taken from VITE_RPC_URL (without trailing slash)
 *   - Each call allows an explicit baseUrl override
 *
 * Notes:
 *   - POST supports JSON (base64 data) and transparently falls back to multipart/form-data.
 *   - All functions support a timeout and shape errors consistently.
 */

export type Hex = `0x${string}`;

export type PutBlobRequest = {
  /** Namespace id (integer in configured valid range). */
  namespace: number | bigint | string;
  /** Blob payload: Uint8Array | ArrayBuffer | Blob | File | string (UTF-8). */
  data: Uint8Array | ArrayBuffer | Blob | File | string;
  /** Optional MIME type; purely informational today. */
  mime?: string;
  /** Override base RPC URL; defaults to import.meta.env.VITE_RPC_URL. */
  baseUrl?: string;
  /** Request timeout in ms (default 20000). */
  timeoutMs?: number;
};

export type PutBlobResponse = {
  commitment: Hex;
  /** Server-provided receipt/metadata if available. */
  receipt?: Record<string, unknown>;
};

export type GetBlobRequest = {
  commitment: Hex | string;
  baseUrl?: string;
  timeoutMs?: number;
};

export type GetBlobResponse = {
  /** Raw blob bytes. */
  data: Uint8Array;
  /** Convenience: a browser Blob with best-effort content-type. */
  blob: Blob;
  /** Optional server metadata via headers (if provided). */
  contentType?: string | null;
  contentLength?: number | null;
};

export type GetProofRequest = {
  commitment: Hex | string;
  /** Optional sample count hint; server may ignore. */
  samples?: number;
  baseUrl?: string;
  timeoutMs?: number;
};

export type GetProofResponse = {
  /** NMT root / DA root associated with the blob set. */
  root: Hex;
  /** Server proof object; shape matches node's DA schema. */
  proof: unknown;
  /** Indices or coordinates sampled, if provided. */
  samples?: number[] | unknown[];
};

export class DAError extends Error {
  status: number;
  code?: string;
  retryAfterMs?: number;

  constructor(message: string, status = 0, code?: string, retryAfterMs?: number) {
    super(message);
    this.name = 'DAError';
    this.status = status;
    this.code = code;
    this.retryAfterMs = retryAfterMs;
  }
}

const DEFAULT_TIMEOUT_MS = 20_000;

function baseUrl(override?: string): string {
  const env = (import.meta as any).env?.VITE_RPC_URL as string | undefined;
  const url = (override ?? env)?.replace(/\/+$/, '');
  if (!url) throw new Error('DA base URL not configured. Set VITE_RPC_URL or pass baseUrl.');
  return url;
}

function parseRetryAfter(h: string | null): number | undefined {
  if (!h) return;
  const n = Number(h);
  if (Number.isFinite(n) && n >= 0) return Math.round(n * 1000);
}

function toUint8(data: PutBlobRequest['data']): Promise<Uint8Array> | Uint8Array {
  if (typeof data === 'string') {
    // UTF-8 encode
    return new TextEncoder().encode(data);
  }
  if (data instanceof Blob) {
    return (async () => new Uint8Array(await data.arrayBuffer()))();
  }
  if (data instanceof ArrayBuffer) return new Uint8Array(data);
  if (data instanceof Uint8Array) return data;
  // File is a Blob subclass (already covered)
  return data as never;
}

function u8ToBase64(u8: Uint8Array): string {
  // Browser-safe base64 from bytes
  let s = '';
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  // btoa expects binary string
  return btoa(s);
}

async function fetchWithTimeout(input: RequestInfo, init: RequestInit & { timeoutMs?: number }) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), init.timeoutMs ?? DEFAULT_TIMEOUT_MS);
  try {
    const res = await fetch(input, { ...init, signal: ctrl.signal });
    return res;
  } finally {
    clearTimeout(t);
  }
}

async function asJsonOrThrow(res: Response) {
  if (res.ok) return res.json();
  let detail: any = null;
  try {
    detail = await res.json();
  } catch {
    // ignore non-json
  }
  const retryAfterMs = parseRetryAfter(res.headers.get('retry-after'));
  const msg =
    detail?.message ||
    detail?.detail ||
    (res.status === 404
      ? 'Not found'
      : res.status === 413
      ? 'Blob too large'
      : `HTTP ${res.status} error`);
  const code = detail?.code || detail?.error;
  throw new DAError(msg, res.status, code, retryAfterMs);
}

/**
 * Post a blob to DA service.
 * Tries JSON (base64) first; falls back to multipart/form-data if server rejects JSON.
 */
export async function putBlob(req: PutBlobRequest): Promise<PutBlobResponse> {
  const url = `${baseUrl(req.baseUrl)}/da/blob`;
  const ns = String(req.namespace);
  const bytes = await toUint8(req.data);

  // Strategy 1: JSON (base64)
  try {
    const jsonBody = {
      namespace: ns,
      data_base64: u8ToBase64(bytes),
      mime: req.mime,
    };
    const res = await fetchWithTimeout(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(jsonBody),
      timeoutMs: req.timeoutMs,
      credentials: 'omit',
      cache: 'no-store',
    });
    if (!res.ok) {
      // If server dislikes JSON (e.g., 415), fall through to multipart
      if (res.status !== 415) return await asJsonOrThrow(res);
      // else try multipart
    } else {
      const out = await res.json();
      return normalizePut(out);
    }
  } catch (e) {
    // Network error — try multipart once as a fallback
  }

  // Strategy 2: multipart/form-data
  const form = new FormData();
  form.set('namespace', ns);
  if (req.mime) {
    form.set('mime', req.mime);
  }
  // Name helps some servers; include a default filename when possible
  const blob = new Blob([bytes], { type: req.mime || 'application/octet-stream' });
  form.set('file', blob, 'blob.bin');

  const res2 = await fetchWithTimeout(url, {
    method: 'POST',
    body: form,
    timeoutMs: req.timeoutMs,
    credentials: 'omit',
    cache: 'no-store',
  });
  const out2 = await asJsonOrThrow(res2);
  return normalizePut(out2);
}

function normalizePut(obj: any): PutBlobResponse {
  const commitment: Hex = (obj?.commitment || obj?.dataRoot || obj?.nmt_root) as Hex;
  if (!commitment || typeof commitment !== 'string' || !commitment.startsWith('0x')) {
    throw new DAError('Server did not return a valid commitment', 0);
  }
  const receipt = obj?.receipt ?? obj?.meta ?? undefined;
  return { commitment, receipt };
}

/**
 * Retrieve a blob by commitment. Returns both raw bytes and a browser Blob.
 */
export async function getBlob(req: GetBlobRequest): Promise<GetBlobResponse> {
  const commit = String(req.commitment);
  const url = `${baseUrl(req.baseUrl)}/da/blob/${commit}`;

  const res = await fetchWithTimeout(url, {
    method: 'GET',
    timeoutMs: req.timeoutMs,
    credentials: 'omit',
    cache: 'no-store',
  });
  if (!res.ok) await asJsonOrThrow(res);

  const contentType = res.headers.get('content-type');
  const contentLength = res.headers.get('content-length');
  const ab = await res.arrayBuffer();
  const blob = new Blob([ab], { type: contentType ?? 'application/octet-stream' });
  return {
    data: new Uint8Array(ab),
    blob,
    contentType,
    contentLength: contentLength ? Number(contentLength) : null,
  };
}

/**
 * Request an availability proof for a commitment (for light verification).
 */
export async function getProof(req: GetProofRequest): Promise<GetProofResponse> {
  const base = baseUrl(req.baseUrl);
  const p = new URLSearchParams({ commitment: String(req.commitment) });
  if (typeof req.samples === 'number' && Number.isFinite(req.samples) && req.samples > 0) {
    p.set('samples', String(Math.floor(req.samples)));
  }
  const url = `${base}/da/proof?${p.toString()}`;

  const res = await fetchWithTimeout(url, {
    method: 'GET',
    timeoutMs: req.timeoutMs,
    credentials: 'omit',
    cache: 'no-store',
  });
  const body = await asJsonOrThrow(res);
  const root: Hex = (body?.root || body?.da_root || body?.nmt_root) as Hex;
  return { root, proof: body?.proof ?? body, samples: body?.samples };
}

/** Small helper to format a commitment for display (0x… trimmed). */
export function shortCommitment(commitment: Hex | string, head = 10, tail = 6): string {
  const s = String(commitment);
  if (s.length <= head + tail + 2) return s;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

export default { putBlob, getBlob, getProof, DAError, shortCommitment };
