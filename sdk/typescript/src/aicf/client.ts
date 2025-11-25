/**
 * AICF (AI Compute Fund) client — read-only JSON-RPC wrapper + result polling.
 *
 * Scope
 *  - Query jobs and providers via the node's `aicf.*` JSON-RPC methods
 *  - Read deterministic job results via `cap.getResult`
 *  - Helper to await a result with polling
 *  - Utility to derive the canonical task id off-chain when all inputs are known
 *
 * Notes
 *  - Enqueueing AI/Quantum work is done by contracts via capabilities syscalls
 *    and produces a deterministic task id tied to the *transaction and height*.
 *    There is intentionally no public write RPC to enqueue from off-chain here.
 */

import type { RpcClient } from '../tx/send'
import { hexToBytes, bytesToHex, isHex } from '../utils/bytes'
import { sha3_256 } from '../utils/hash'

// ──────────────────────────────────────────────────────────────────────────────
// Types
// ──────────────────────────────────────────────────────────────────────────────

export type JobKind = 'AI' | 'QUANTUM'
export type JobStatus = 'Enqueued' | 'Assigned' | 'Completed' | 'Expired' | 'Failed' | 'Canceled'

export interface JobRecord {
  id: string                       // task_id (0x…)
  kind: JobKind
  caller?: string                  // contract / account address that requested
  height?: number                  // block height when request was accepted
  txHash?: string
  providerId?: string
  status: JobStatus
  createdAt?: string               // ISO8601 (optional, node-specific)
  updatedAt?: string
  // Opaque spec (e.g., model/prompt or circuit/shots); server may redact
  spec?: Record<string, unknown>
  // If completed, nodes may include a compact pointer to proof/result
  resultRef?: Record<string, unknown>
  [k: string]: unknown
}

export interface ResultRecord {
  taskId: string                   // 0x…
  kind: JobKind
  ok: boolean
  // Opaque payload (e.g., output digest, commitments, proof refs, costs)
  result?: Record<string, unknown>
  // Optional references to chain proofs; verify using proofs/ clients
  proofs?: Record<string, unknown>
  // Optional metadata: units, fees, timestamps
  meta?: Record<string, unknown>
  [k: string]: unknown
}

export interface ListJobsParams {
  kind?: JobKind
  status?: JobStatus
  caller?: string
  limit?: number
  cursor?: string
}

export interface ListJobsResponse {
  jobs: JobRecord[]
  nextCursor?: string
}

export interface ProviderRecord {
  id: string
  kind: ('AI' | 'QUANTUM')[]
  stake?: string
  region?: string
  status?: 'Active' | 'Jailed' | 'Cooling'
  score?: number
  caps?: Record<string, unknown>
  [k: string]: unknown
}

// ──────────────────────────────────────────────────────────────────────────────
// Client
// ──────────────────────────────────────────────────────────────────────────────

export class AICFClient {
  constructor(private readonly rpc: RpcClient) {}

  // Providers
  async listProviders(filter?: { kind?: JobKind; region?: string; status?: string }): Promise<ProviderRecord[]> {
    // aicf.listProviders(filter?) → ProviderRecord[]
    return this.callSafe<ProviderRecord[]>('aicf.listProviders', filter ? [filter] : [])
  }

  async getProvider(id: string): Promise<ProviderRecord | null> {
    // aicf.getProvider(id) → ProviderRecord|null
    return this.callSafe<ProviderRecord | null>('aicf.getProvider', [id])
  }

  // Jobs
  async listJobs(params: ListJobsParams = {}): Promise<ListJobsResponse> {
    // aicf.listJobs({kind?, status?, caller?, limit?, cursor?}) → {jobs, nextCursor?}
    const res = await this.callSafe<any>('aicf.listJobs', [params])
    return normalizeListJobs(res)
  }

  async getJob(id: string): Promise<JobRecord | null> {
    // aicf.getJob(id) → JobRecord|null
    const job = await this.callSafe<JobRecord | null>('aicf.getJob', [id])
    return job ? normalizeJob(job) : null
  }

  // Results (read-only via capabilities bridge)
  async getResult(taskId: string): Promise<ResultRecord | null> {
    // cap.getResult(taskId) → ResultRecord|null
    const id = normalizeHex(taskId)
    const rec = await this.callSafe<ResultRecord | null>('cap.getResult', [id])
    return rec ? normalizeResult(rec) : null
  }

  /**
   * Await a job result by polling `cap.getResult` until it returns a record,
   * or until `timeoutMs` is exceeded (default 60s). Returns the record or null
   * on timeout.
   */
  async awaitResult(
    taskId: string,
    opts: { intervalMs?: number; timeoutMs?: number } = {}
  ): Promise<ResultRecord | null> {
    const interval = Math.max(200, opts.intervalMs ?? 1000)
    const timeout = Math.max(interval, opts.timeoutMs ?? 60_000)
    const start = Date.now()
    const id = normalizeHex(taskId)

    // First quick attempt
    const first = await this.getResult(id)
    if (first) return first

    while (Date.now() - start < timeout) {
      await sleep(interval)
      const r = await this.getResult(id)
      if (r) return r
    }
    return null
  }

  /**
   * Deterministically derive a task id (0x…) given chainId, height, txHash,
   * caller address, and the *exact* payload bytes that were enqueued.
   *
   * This mirrors capabilities/jobs/id.py:
   *   task_id = H("cap.task_id.v1" | chainId | height | txHash | caller | payload)
   *
   * Use only when all inputs are known; otherwise you cannot predict the id.
   */
  static deriveTaskId(
    chainId: number,
    height: number,
    txHash: string,
    caller: string,
    payload: Uint8Array | string
  ): string {
    const dom = text('cap.task_id.v1')
    const cid = u64(chainId)
    const hgt = u64(height)
    const tx = hexToBytes(normalizeHex(txHash))
    const addr = encodeAddress(caller)
    const p = toBytes(payload)
    // Simple concatenation with fixed-width integers
    const data = concat(dom, cid, hgt, tx, addr, p)
    return '0x' + bytesToHex(sha3_256(data))
  }

  // Low-level wrapper with friendly errors
  private async callSafe<T = unknown>(method: string, params: unknown[]): Promise<T> {
    try {
      // RpcClient from sdk provides .call(method, params)
      // If your client differs, adapt here.
      // @ts-ignore - runtime shape has call()
      return await this.rpc.call(method, params)
    } catch (e: any) {
      const msg = e?.message || String(e)
      throw new Error(`AICFClient: RPC ${method} failed: ${msg}`)
    }
  }
}

// ──────────────────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────────────────

function normalizeListJobs(x: any): ListJobsResponse {
  const jobs = Array.isArray(x?.jobs) ? x.jobs.map(normalizeJob) : []
  const nextCursor = typeof x?.nextCursor === 'string' ? x.nextCursor : undefined
  return { jobs, nextCursor }
}

function normalizeJob(j: any): JobRecord {
  const id = normalizeHex(j?.id || j?.taskId || '')
  const kind = (j?.kind || '').toString().toUpperCase()
  const status = (j?.status || '').toString()
  const out: JobRecord = {
    id,
    kind: kind === 'AI' || kind === 'QUANTUM' ? (kind as JobKind) : 'AI',
    caller: j?.caller,
    height: safeNum(j?.height),
    txHash: j?.txHash ? normalizeHex(j.txHash) : undefined,
    providerId: j?.providerId ?? j?.provider_id,
    status: (['Enqueued','Assigned','Completed','Expired','Failed','Canceled'].includes(status) ? status : 'Enqueued') as JobStatus,
    createdAt: asIso(j?.createdAt ?? j?.created_at),
    updatedAt: asIso(j?.updatedAt ?? j?.updated_at),
    spec: isObj(j?.spec) ? j.spec : undefined,
    resultRef: isObj(j?.resultRef) ? j.resultRef : undefined
  }
  // copy-through any additional fields
  for (const [k, v] of Object.entries(j || {})) {
    if (!(k in out)) (out as any)[k] = v
  }
  return out
}

function normalizeResult(r: any): ResultRecord {
  const id = normalizeHex(r?.taskId || r?.id || '')
  const kind = (r?.kind || '').toString().toUpperCase()
  const out: ResultRecord = {
    taskId: id,
    kind: kind === 'AI' || kind === 'QUANTUM' ? (kind as JobKind) : 'AI',
    ok: !!r?.ok,
    result: isObj(r?.result) ? r.result : undefined,
    proofs: isObj(r?.proofs) ? r.proofs : undefined,
    meta: isObj(r?.meta) ? r.meta : undefined
  }
  for (const [k, v] of Object.entries(r || {})) {
    if (!(k in out)) (out as any)[k] = v
  }
  return out
}

function normalizeHex(h: string): string {
  if (!h) throw new Error('Expected 0x-hex string')
  const s = h.startsWith('0x') || h.startsWith('0X') ? h : ('0x' + h)
  if (!isHex(s)) throw new Error(`Invalid hex: ${h}`)
  return s.toLowerCase()
}

function safeNum(x: any): number | undefined {
  const n = Number(x)
  return Number.isFinite(n) ? n : undefined
}

function asIso(x: any): string | undefined {
  if (typeof x === 'string' && x.length) return x
  return undefined
}

function isObj(x: any): x is Record<string, unknown> {
  return x && typeof x === 'object' && !Array.isArray(x)
}

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms))
}

function text(s: string): Uint8Array {
  return new TextEncoder().encode(s)
}

function toBytes(x: Uint8Array | string): Uint8Array {
  if (x instanceof Uint8Array) return x
  if (isHex(x)) return hexToBytes(x)
  return new TextEncoder().encode(x)
}

function u64(n: number): Uint8Array {
  const buf = new Uint8Array(8)
  const dv = new DataView(buf.buffer)
  dv.setBigUint64(0, BigInt(n >>> 0) + (BigInt(Math.floor(n / 2 ** 32)) << 32n), false) // big-endian
  return buf
}

function encodeAddress(addr: string): Uint8Array {
  // Accept bech32m or hex. For hex: left-pad to 32 bytes. For bech32m: hash string to 32 bytes.
  if (addr.startsWith('0x') || addr.startsWith('0X')) {
    const raw = hexToBytes(addr)
    if (raw.length === 20) return padLeft(raw, 32)
    if (raw.length === 32) return raw
    return padLeft(raw, 32)
  }
  // Bech32m or other user-facing forms — normalize by hashing text bytes
  return sha3_256(text(addr))
}

function padLeft(b: Uint8Array, len: number): Uint8Array {
  if (b.length >= len) return b
  const out = new Uint8Array(len)
  out.set(b, len - b.length)
  return out
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const len = parts.reduce((n, p) => n + p.length, 0)
  const out = new Uint8Array(len)
  let off = 0
  for (const p of parts) { out.set(p, off); off += p.length }
  return out
}

export default {
  AICFClient
}
