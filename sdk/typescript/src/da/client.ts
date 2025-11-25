/**
 * Data Availability (DA) client — REST-style helper for posting/retrieving blobs
 * and fetching availability proofs from a node that mounts the DA service.
 *
 * Endpoints (as mounted by the node's FastAPI app):
 *   - POST   /da/blob                      → { commitment: 0x…, size, namespace, receipt? }
 *   - GET    /da/blob/{commitment}         → raw blob bytes (application/octet-stream)
 *   - GET    /da/proof?commitment=0x…      → JSON proof object (shape is opaque to SDK)
 *
 * Notes:
 *  - This client is REST-oriented (not JSON-RPC). Pass the base URL of your node
 *    (e.g. http://127.0.0.1:8545). You can also pass an HttpRpcClient from
 *    `../rpc/http` — we will try to read its `endpoint` string as base URL.
 *  - Blob data may be provided as `Uint8Array` or as a `0x…` hex string.
 *  - All commitments are 0x-prefixed hex strings (lower/upper-safe).
 */

import { bytesToHex, hexToBytes, isHex } from '../utils/bytes'
import { sha3_256 } from '../utils/hash'

export interface BlobReceipt {
  /** Namespaced Merkle (NMT) commitment for the blob (0x…) */
  commitment: string
  /** Blob size in bytes */
  size: number
  /** Namespace id (uint32 or similar, chain-specific range) */
  namespace: number
  /** Optional server-side receipt/meta (opaque to SDK) */
  receipt?: Record<string, unknown>
}

export interface ProofResponse {
  /** The commitment the proof corresponds to (echo) */
  commitment: string
  /** Opaque proof object; verify on-chain or via light client utilities. */
  proof: unknown
  /** Optional sample indices used to construct the proof. */
  samples?: number[]
  /** Optional auxiliary fields (roots, codec, etc.). */
  [k: string]: unknown
}

export interface DAClientOptions {
  /** Base URL for the node or DA service, e.g. "http://127.0.0.1:8545". */
  baseUrl?: string
  /** If provided, will be attached as `Authorization: Bearer <token>` on requests. */
  apiKey?: string
  /** Additional headers to send with every request. */
  headers?: Record<string, string>
}

export type BaseLike = string | { endpoint?: string } // HttpRpcClient compat

export class DAClient {
  readonly baseUrl: string
  private readonly headers: Record<string, string>

  constructor(base: BaseLike, opts: DAClientOptions = {}) {
    const fromCtor = typeof base === 'string' ? base : (base?.endpoint || '')
    const baseUrl = opts.baseUrl || fromCtor
    if (!baseUrl) throw new Error('DAClient: baseUrl is required')
    this.baseUrl = stripTrailingSlash(baseUrl)
    this.headers = {
      ...(opts.headers || {}),
      ...(opts.apiKey ? { Authorization: `Bearer ${opts.apiKey}` } : {})
    }
  }

  /**
   * POST /da/blob
   * Upload a blob under a namespace. Returns the server-computed commitment and a receipt.
   */
  async postBlob(namespace: number, data: Uint8Array | string, mime?: string): Promise<BlobReceipt> {
    const body = {
      namespace,
      data: toHex(data), // server accepts 0x-hex encoded payload
      mime: mime || 'application/octet-stream',
      // Include a content hash for integrity hints (server may ignore)
      contentHash: '0x' + bytesToHex(sha3_256(toBytes(data)))
    }
    const res = await fetch(`${this.baseUrl}/da/blob`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        ...this.headers
      },
      body: JSON.stringify(body)
    })
    await ensureOk(res, 'Failed to POST /da/blob')
    const json = await res.json()
    return normalizeReceipt(json)
  }

  /**
   * GET /da/blob/{commitment}
   * Retrieve raw blob bytes by commitment.
   */
  async getBlob(commitment: string): Promise<Uint8Array> {
    const url = `${this.baseUrl}/da/blob/${normalizeCommitment(commitment)}`
    const res = await fetch(url, { headers: this.headers })
    await ensureOk(res, 'Failed to GET /da/blob/{commitment}')
    // Prefer binary; server may also return JSON { data: 0x… } as a fallback.
    const ct = res.headers.get('content-type') || ''
    if (ct.includes('application/json')) {
      const j = await res.json()
      const d = (j && (j.data || j.bytes || j.blob)) as string | undefined
      if (!d || !isHex(d)) throw new Error('DAClient: JSON blob response missing hex data')
      return hexToBytes(d)
    }
    const buf = new Uint8Array(await res.arrayBuffer())
    return buf
  }

  /**
   * GET /da/proof?commitment=0x…
   * Fetch a DAS proof for an existing commitment.
   * Some deployments may alternatively expose /da/blob/{commitment}/proof — we try both.
   */
  async getProof(commitment: string, samples?: number[]): Promise<ProofResponse> {
    const c = normalizeCommitment(commitment)
    const query = new URLSearchParams({ commitment: c })
    if (samples && samples.length) query.set('samples', samples.join(','))
    // First try the canonical form:
    let res = await fetch(`${this.baseUrl}/da/proof?${query.toString()}`, { headers: this.headers })
    if (res.status === 404) {
      // Fallback path
      const qs = samples && samples.length ? `?samples=${samples.join(',')}` : ''
      res = await fetch(`${this.baseUrl}/da/blob/${c}/proof${qs}`, { headers: this.headers })
    }
    await ensureOk(res, 'Failed to GET /da/proof')
    const json = await res.json()
    return normalizeProof(json)
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Utilities
// ──────────────────────────────────────────────────────────────────────────────

function toHex(x: Uint8Array | string): string {
  if (typeof x === 'string') {
    if (isHex(x)) return x.startsWith('0x') ? x : ('0x' + x)
    return '0x' + bytesToHex(new TextEncoder().encode(x))
  }
  return '0x' + bytesToHex(x)
}

function toBytes(x: Uint8Array | string): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (isHex(x)) return hexToBytes(x)
  return new TextEncoder().encode(x)
}

async function ensureOk(res: Response, msg: string) {
  if (!res.ok) {
    let extra = ''
    try {
      const t = await res.text()
      extra = t ? ` — ${t}` : ''
    } catch { /* ignore */ }
    throw new Error(`${msg}: ${res.status} ${res.statusText}${extra}`)
  }
}

function stripTrailingSlash(u: string): string {
  return u.endsWith('/') ? u.slice(0, -1) : u
}

function normalizeCommitment(c: string): string {
  const s = c.startsWith('0x') || c.startsWith('0X') ? c : ('0x' + c)
  return s.toLowerCase()
}

function normalizeReceipt(j: any): BlobReceipt {
  if (!j || typeof j !== 'object') throw new Error('DAClient: invalid receipt payload')
  const commitment = normalizeCommitment(j.commitment || j.commit || '')
  if (!isHex(commitment)) throw new Error('DAClient: receipt missing commitment')
  const size = Number(j.size ?? j.length ?? 0)
  const namespace = Number(j.namespace ?? j.ns ?? 0)
  const receipt = j.receipt && typeof j.receipt === 'object' ? j.receipt : undefined
  return { commitment, size, namespace, receipt }
}

function normalizeProof(j: any): ProofResponse {
  if (!j || typeof j !== 'object') throw new Error('DAClient: invalid proof payload')
  const commitment = normalizeCommitment(j.commitment || j.commit || '')
  const proof = j.proof ?? j
  const samples = Array.isArray(j.samples) ? j.samples.map((x: any) => Number(x)) : undefined
  return { commitment, proof, samples, ...j }
}

export default {
  DAClient
}
