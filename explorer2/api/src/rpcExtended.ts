/**
 * Extended RPC client methods for AICF, Mining, DA, Quantum, and RPC Inspector.
 * All methods degrade gracefully — "method not found" returns null/empty, not an error.
 */

import { RpcClient, RpcError } from './rpcClient.js'
import pino from 'pino'

const log = pino({ name: 'rpc-extended' })

/** Test whether a rejection is a "method not found" RPC error. */
function isMethodNotFound(err: unknown): boolean {
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase()
  return (
    msg.includes('method not found') ||
    msg.includes('unknown method') ||
    msg.includes('not implemented') ||
    (err instanceof RpcError && (err.code === -32601 || err.code === -32600))
  )
}

/** Try an RPC call; return null if method not available; throw on real errors. */
async function tryCall<T>(rpc: RpcClient, method: string, params: unknown[] = []): Promise<T | null> {
  try {
    return await rpc.call<T>(method, params)
  } catch (err) {
    if (isMethodNotFound(err)) return null
    log.warn({ method, err: err instanceof Error ? err.message : String(err) }, 'RPC call failed')
    return null
  }
}

// ── RPC Inspector ─────────────────────────────────────────────────────────────

export interface RpcDiscoverResult {
  available: boolean
  methods: string[]
  servers?: unknown[]
  version?: string
  raw?: unknown
}

export async function rpcDiscover(rpc: RpcClient): Promise<RpcDiscoverResult> {
  // Try rpc.discover first, then rpc.listMethods, then node.ping
  const discovered = await tryCall<unknown>(rpc, 'rpc.discover', [])
  if (discovered !== null) {
    const raw = discovered as Record<string, unknown>
    const methods: string[] = []
    if (Array.isArray(raw?.methods)) {
      for (const m of raw.methods as unknown[]) {
        if (typeof m === 'string') methods.push(m)
        else if (typeof (m as Record<string, unknown>)?.name === 'string') methods.push((m as Record<string, string>).name)
      }
    }
    return {
      available: true,
      methods,
      servers: Array.isArray(raw?.servers) ? raw.servers : undefined,
      version: typeof raw?.version === 'string' ? raw.version : undefined,
      raw: discovered
    }
  }

  const listed = await tryCall<unknown>(rpc, 'rpc.listMethods', [])
  if (listed !== null) {
    const raw = listed as Record<string, unknown>
    let methods: string[]
    let version: string | undefined
    if (Array.isArray(listed)) {
      methods = (listed as string[]).filter(m => typeof m === 'string')
    } else {
      methods = Array.isArray(raw?.methods) ? (raw.methods as string[]).filter(m => typeof m === 'string') : []
      version = typeof raw?.version === 'string' ? raw.version : undefined
    }
    return { available: true, methods, version, raw: listed }
  }

  // Minimal ping check
  const ping = await tryCall<unknown>(rpc, 'node.ping', [])
  if (ping !== null) {
    return { available: true, methods: ['node.ping'] }
  }

  return { available: false, methods: [] }
}

// ── Network / Service Status ──────────────────────────────────────────────────

export interface ServiceStatus {
  timestamp: string
  services: {
    name: string
    status: 'ok' | 'degraded' | 'down' | 'unknown'
    hint?: string
    remediation?: string
    detail?: unknown
  }[]
}

export async function getServiceStatus(rpc: RpcClient): Promise<ServiceStatus> {
  const checks = await Promise.allSettled([
    tryCall<unknown>(rpc, 'admin.serviceStatus', []),
    tryCall<unknown>(rpc, 'node.getStatus', []),
    tryCall<unknown>(rpc, 'chain.getHead', []),
    tryCall<unknown>(rpc, 'mempool.getStats', []),
    tryCall<unknown>(rpc, 'aicf.getStatus', []),
    tryCall<unknown>(rpc, 'da.getStatus', []),
    tryCall<unknown>(rpc, 'miner.getStatus', []),
    tryCall<unknown>(rpc, 'quantum.getStatus', []),
  ])

  const [adminStatus, nodeStatus, chainHead, mempoolStats, aicfStatus, daStatus, minerStatus, quantumStatus] = checks

  const services: ServiceStatus['services'] = []

  // Chain
  const headOk = chainHead.status === 'fulfilled' && chainHead.value !== null
  services.push({
    name: 'chain',
    status: headOk ? 'ok' : 'down',
    hint: headOk ? undefined : 'Chain head is not accessible via RPC',
    remediation: headOk ? undefined : 'Check the node is running and EXPLORER2_RPC_URL is correct'
  })

  // Mempool
  const mempoolOk = mempoolStats.status === 'fulfilled' && mempoolStats.value !== null
  services.push({
    name: 'mempool',
    status: mempoolOk ? 'ok' : 'unknown',
    hint: mempoolOk ? undefined : 'Mempool stats not available',
    detail: mempoolOk ? mempoolStats.value : undefined
  })

  // AICF
  const aicfOk = aicfStatus.status === 'fulfilled' && aicfStatus.value !== null
  services.push({
    name: 'aicf',
    status: aicfOk ? 'ok' : 'unknown',
    hint: aicfOk ? undefined : 'AICF status not available on this node',
    detail: aicfOk ? aicfStatus.value : undefined
  })

  // DA
  const daOk = daStatus.status === 'fulfilled' && daStatus.value !== null
  services.push({
    name: 'da',
    status: daOk ? 'ok' : 'unknown',
    hint: daOk ? undefined : 'DA status not available on this node',
    detail: daOk ? daStatus.value : undefined
  })

  // Miner
  const minerOk = minerStatus.status === 'fulfilled' && minerStatus.value !== null
  services.push({
    name: 'miner',
    status: minerOk ? 'ok' : 'unknown',
    hint: minerOk ? undefined : 'Miner status not available on this node',
    detail: minerOk ? minerStatus.value : undefined
  })

  // Quantum
  const quantumOk = quantumStatus.status === 'fulfilled' && quantumStatus.value !== null
  services.push({
    name: 'quantum',
    status: quantumOk ? 'ok' : 'unknown',
    hint: quantumOk ? undefined : 'Quantum worker status not available on this node',
    detail: quantumOk ? quantumStatus.value : undefined
  })

  // If admin/node status provides enriched data, merge it
  const richStatus = (adminStatus.status === 'fulfilled' && adminStatus.value) ||
                     (nodeStatus.status === 'fulfilled' && nodeStatus.value)
  if (richStatus && typeof richStatus === 'object') {
    const rich = richStatus as Record<string, unknown>
    for (const svc of services) {
      if (rich[svc.name] !== undefined) {
        svc.detail = { ...((svc.detail as object) ?? {}), nodeReported: rich[svc.name] }
      }
    }
  }

  return { timestamp: new Date().toISOString(), services }
}

// ── AICF ─────────────────────────────────────────────────────────────────────

export interface AICFInfo {
  available: boolean
  status?: unknown
  credits?: unknown
  jobs?: unknown
  plans?: unknown
}

export async function getAICFInfo(rpc: RpcClient, address?: string): Promise<AICFInfo> {
  const [status, credits, jobs, plans] = await Promise.all([
    tryCall<unknown>(rpc, 'aicf.getStatus', []),
    address ? tryCall<unknown>(rpc, 'aicf.getCredits', [address]) : Promise.resolve(null),
    tryCall<unknown>(rpc, 'aicf.listJobs', [{ limit: 20 }]),
    tryCall<unknown>(rpc, 'aicf.listPlans', []),
  ])

  const available = status !== null || jobs !== null || plans !== null

  return { available, status, credits, jobs, plans }
}

// ── Mining ────────────────────────────────────────────────────────────────────

export interface MiningInfo {
  available: boolean
  status?: unknown
  template?: unknown
  metrics?: unknown
}

export async function getMiningInfo(rpc: RpcClient): Promise<MiningInfo> {
  const [status, template, metrics] = await Promise.all([
    tryCall<unknown>(rpc, 'miner.getStatus', []),
    tryCall<unknown>(rpc, 'miner.getBlockTemplate', []),
    tryCall<unknown>(rpc, 'miner.getMetrics', []),
  ])

  const available = status !== null || template !== null

  return { available, status, template, metrics }
}

// ── DA ────────────────────────────────────────────────────────────────────────

export interface DAInfo {
  available: boolean
  status?: unknown
  quotas?: unknown
}

export async function getDAInfo(rpc: RpcClient): Promise<DAInfo> {
  const [status, quotas] = await Promise.all([
    tryCall<unknown>(rpc, 'da.getStatus', []),
    tryCall<unknown>(rpc, 'da.getQuotas', []),
  ])

  const available = status !== null

  return { available, status, quotas }
}

export async function daGetBlob(rpc: RpcClient, commitment: string): Promise<unknown> {
  return tryCall<unknown>(rpc, 'da.getBlob', [commitment])
}

export async function daGetProof(rpc: RpcClient, commitment: string): Promise<unknown> {
  return tryCall<unknown>(rpc, 'da.getProof', [commitment])
}

export async function daPutBlob(rpc: RpcClient, namespace: string, data: string): Promise<unknown> {
  // da.putBlob is a write method — no retry; single attempt only
  try {
    return await rpc.call('da.putBlob', [namespace, data])
  } catch (err) {
    if (isMethodNotFound(err)) return null
    throw err
  }
}

export async function daListHistory(rpc: RpcClient, limit = 20): Promise<unknown[]> {
  const result = await tryCall<unknown>(rpc, 'da.listCommitments', [limit])
  if (Array.isArray(result)) return result
  if (result && typeof result === 'object' && Array.isArray((result as Record<string, unknown>).items)) {
    return (result as Record<string, unknown[]>).items
  }
  return []
}

// ── Quantum ───────────────────────────────────────────────────────────────────

export interface QuantumInfo {
  available: boolean
  status?: unknown
  workers?: unknown
  jobs?: unknown
  policy?: unknown
}

export async function getQuantumInfo(rpc: RpcClient): Promise<QuantumInfo> {
  const [status, workers, jobs, policy] = await Promise.all([
    tryCall<unknown>(rpc, 'quantum.getStatus', []),
    tryCall<unknown>(rpc, 'quantum.listWorkers', []),
    tryCall<unknown>(rpc, 'quantum.listJobs', [{ limit: 20 }]),
    tryCall<unknown>(rpc, 'quantum.getPolicy', []),
  ])

  const available = status !== null || workers !== null || jobs !== null

  return { available, status, workers, jobs, policy }
}
