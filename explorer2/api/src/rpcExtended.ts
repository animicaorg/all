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
async function tryCall<T>(rpc: RpcClient, method: string, params?: unknown[] | Record<string, unknown>): Promise<T | null> {
  try {
    return await rpc.call<T>(method, params)
  } catch (err) {
    if (isMethodNotFound(err)) return null
    log.warn({ method, err: err instanceof Error ? err.message : String(err) }, 'RPC call failed')
    return null
  }
}

/**
 * Probe a method and distinguish "not found" from "real error".
 * Returns { notFound: true } when method doesn't exist.
 * Returns { notFound: false, value, error } when method exists (value may be null on error).
 */
async function probeMethod<T>(
  rpc: RpcClient,
  method: string,
  params?: unknown[] | Record<string, unknown>
): Promise<{ notFound: true } | { notFound: false; value: T | null; error: Error | null }> {
  try {
    const value = await rpc.call<T>(method, params)
    return { notFound: false, value, error: null }
  } catch (err) {
    if (isMethodNotFound(err)) return { notFound: true }
    const error = err instanceof Error ? err : new Error(String(err))
    log.warn({ method, err: error.message }, 'RPC probe failed')
    return { notFound: false, value: null, error }
  }
}

// ── RPC Capabilities ──────────────────────────────────────────────────────────

/** Capability booleans derived from rpc.discover. */
export interface RpcCapabilitiesResult {
  supportsAicfStatus: boolean
  supportsDaStatus: boolean
  supportsMinerStatus: boolean
  supportsQuantumStatus: boolean
  supportsMempoolStatus: boolean
  /** Whether capability data comes from rpc.discover (true) or individual probes (false). */
  fromDiscover: boolean
  /** All methods known to the node (may be empty if discover not available). */
  knownMethods: Set<string>
}

/** Cache entry for capability detection. TTL = 60 seconds. */
interface CapabilityCache {
  result: RpcCapabilitiesResult
  expiresAt: number
}

const CAPABILITY_CACHE_TTL_MS = 60_000
// Use a WeakMap key'd by RpcClient instance so different nodes don't share cache.
const capabilityCache = new WeakMap<RpcClient, CapabilityCache>()

/** Canonical aliases checked per service (order: preferred first). */
const AICF_STATUS_METHODS = ['aicf.status', 'aicf_status', 'aicf.getStatus', 'aicf_getStatus']
const DA_STATUS_METHODS = ['da.status', 'da_status', 'da.getStatus', 'da_getStatus']
const MINER_STATUS_METHODS = ['miner.status', 'miner_status', 'miner.getStatus', 'miner_getStatus', 'mining.getTemplateStatus']
const QUANTUM_STATUS_METHODS = ['quantum.status', 'quantum_status', 'quantum.getStatus', 'quantum_getStatus', 'quantum.workerStatus']
const MEMPOOL_STATUS_METHODS = ['mempool.getStats', 'mempool_getStats', 'mempool.status']

/** Derive capabilities from a known methods set. */
function deriveCaps(known: Set<string>): Omit<RpcCapabilitiesResult, 'fromDiscover' | 'knownMethods'> {
  return {
    supportsAicfStatus: AICF_STATUS_METHODS.some(m => known.has(m)),
    supportsDaStatus: DA_STATUS_METHODS.some(m => known.has(m)),
    supportsMinerStatus: MINER_STATUS_METHODS.some(m => known.has(m)),
    supportsQuantumStatus: QUANTUM_STATUS_METHODS.some(m => known.has(m)),
    supportsMempoolStatus: MEMPOOL_STATUS_METHODS.some(m => known.has(m)),
  }
}

/**
 * Detect RPC capabilities for AICF, DA, Miner, Quantum, Mempool.
 * Calls rpc.discover once and caches the result for 60 seconds.
 * Falls back to per-method probing if discover is unavailable.
 */
export async function getRpcCapabilities(rpc: RpcClient): Promise<RpcCapabilitiesResult> {
  const now = Date.now()
  const cached = capabilityCache.get(rpc)
  if (cached && cached.expiresAt > now) {
    return cached.result
  }

  // Try rpc.discover to get list of methods.
  const discoverResult = await rpcDiscover(rpc)
  if (discoverResult.available && discoverResult.methods.length > 0) {
    const known = new Set(discoverResult.methods)
    const result: RpcCapabilitiesResult = {
      ...deriveCaps(known),
      fromDiscover: true,
      knownMethods: known,
    }
    capabilityCache.set(rpc, { result, expiresAt: now + CAPABILITY_CACHE_TTL_MS })
    return result
  }

  // discover not available — probe each service's primary method individually.
  const [aicfProbe, daProbe, minerProbe, quantumProbe, mempoolProbe] = await Promise.all([
    probeMethod(rpc, 'aicf.status'),
    probeMethod(rpc, 'da.status'),
    probeMethod(rpc, 'miner.status'),
    probeMethod(rpc, 'quantum.status'),
    probeMethod(rpc, 'mempool.getStats'),
  ])

  // Build a synthetic known-methods set from probes.
  const known = new Set<string>()
  if (!aicfProbe.notFound) known.add('aicf.status')
  if (!daProbe.notFound) known.add('da.status')
  if (!minerProbe.notFound) known.add('miner.status')
  if (!quantumProbe.notFound) known.add('quantum.status')
  if (!mempoolProbe.notFound) known.add('mempool.getStats')

  const result: RpcCapabilitiesResult = {
    ...deriveCaps(known),
    fromDiscover: false,
    knownMethods: known,
  }
  capabilityCache.set(rpc, { result, expiresAt: now + CAPABILITY_CACHE_TTL_MS })
  return result
}

/** Invalidate the capability cache for a given RPC client (e.g. after reconnect). */
export function invalidateCapabilityCache(rpc: RpcClient): void {
  capabilityCache.delete(rpc)
}


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
    /** ok=healthy, degraded=partial, down=error, not_supported=method absent (not an error), unknown=probe inconclusive */
    status: 'ok' | 'degraded' | 'down' | 'unknown' | 'not_supported'
    hint?: string
    remediation?: string
    detail?: unknown
  }[]
}

/**
 * Standard status schema returned by aicf.status, da.status, miner.status, quantum.status.
 * Nodes that implement these methods return this shape.
 */
interface NodeStatusPayload {
  enabled?: boolean
  ok?: boolean
  reason?: string | null
  message?: string | null
  details?: unknown
}

const ENABLE_HINTS: Record<string, string> = {
  aicf: 'Enable AICF by configuring pool/module settings and exposing the AICF RPC methods.',
  da: 'Enable DA via ANIMICA_DA_ENABLED=1, set ANIMICA_DA_STORAGE_DIR, and mount it read-write.',
  quantum: 'Enable Quantum via ANIMICA_MINER_QUANTUM_WORKER=1 and start the worker process.',
}

/** Map a NodeStatusPayload to a ServiceStatus entry status. */
function mapNodeStatus(payload: NodeStatusPayload, serviceName?: string): {
  status: ServiceStatus['services'][number]['status']
  hint: string | undefined
  remediation: string | undefined
} {
  if (!payload.enabled) {
    const reason = payload.reason ?? 'disabled'
    return {
      status: 'not_supported',
      hint: payload.message ?? `Disabled/Not configured on this node (reason: ${reason})`,
      remediation: undefined,
    }
  }
  if (payload.ok) {
    return { status: 'ok', hint: undefined, remediation: undefined }
  }
  const reason = payload.reason ?? 'unknown'
  let remediation: string | undefined
  if (reason === 'read_only') {
    remediation = 'Check file permissions on the data directory (mount as read-write in docker/k8s)'
  } else if (reason === 'dependency_missing') {
    remediation = 'Ensure all required node modules are installed and enabled'
  }
  return {
    status: 'degraded',
    hint: payload.message ?? `Service error (reason: ${reason})`,
    remediation,
  }
}

export async function getServiceStatus(rpc: RpcClient): Promise<ServiceStatus> {
  // First, detect capabilities to distinguish "not supported" from "down".
  const caps = await getRpcCapabilities(rpc)

  // Always probe chain (required for basic operation).
  const [adminStatus, nodeStatus, chainHead, mempoolProbe] = await Promise.allSettled([
    tryCall<unknown>(rpc, 'admin.serviceStatus', []),
    tryCall<unknown>(rpc, 'node.getStatus', []),
    probeMethod<unknown>(rpc, 'chain.getHead'),
    caps.supportsMempoolStatus
      ? probeMethod<NodeStatusPayload>(rpc, 'mempool.getStats')
      : Promise.resolve({ notFound: true } as const),
  ])

  const services: ServiceStatus['services'] = []

  // Chain
  const chainResult = chainHead.status === 'fulfilled' ? chainHead.value : null
  const headOk = chainResult !== null && !chainResult.notFound && chainResult.value !== null
  services.push({
    name: 'chain',
    status: headOk ? 'ok' : 'down',
    hint: headOk ? undefined : 'Chain head is not accessible via RPC',
    remediation: headOk ? undefined : 'Check the node is running and EXPLORER2_RPC_URL is correct',
  })

  // Mempool
  if (!caps.supportsMempoolStatus) {
    services.push({
      name: 'mempool',
      status: 'not_supported',
      hint: 'Not supported by this RPC',
    })
  } else {
    const mempoolResult = mempoolProbe.status === 'fulfilled' ? mempoolProbe.value : null
    if (mempoolResult === null || mempoolResult.notFound) {
      services.push({ name: 'mempool', status: 'not_supported', hint: 'Not supported by this RPC' })
    } else if (mempoolResult.error) {
      services.push({
        name: 'mempool',
        status: 'down',
        hint: `Mempool error: ${mempoolResult.error.message}`,
      })
    } else {
      services.push({
        name: 'mempool',
        status: 'ok',
        detail: mempoolResult.value ?? undefined,
      })
    }
  }

  // Helper to probe a service status method using the preferred aliases.
  const probeServiceStatus = async (
    supported: boolean,
    primaryMethod: string,
    aliases: string[],
    serviceName: string,
  ): Promise<ServiceStatus['services'][number]> => {
    if (!supported) {
      return {
        name: serviceName,
        status: 'not_supported',
        hint: 'Not supported by this RPC',
      }
    }

    // Try methods in alias order — use the first one that the node knows about.
    const methodToUse = aliases.find(m => caps.knownMethods.has(m)) ?? primaryMethod

    if (process.env.NODE_ENV !== 'production') {
      log.info({ service: serviceName, method: methodToUse, params: null }, 'Explorer2 status RPC probe')
    }
    const probe = await probeMethod<NodeStatusPayload>(rpc, methodToUse)
    if (probe.notFound) {
      return { name: serviceName, status: 'not_supported', hint: 'Not supported by this RPC' }
    }
    if (probe.error) {
      return {
        name: serviceName,
        status: 'down',
        hint: `Service error: ${probe.error.message}`,
      }
    }
    if (!probe.value) {
      return { name: serviceName, status: 'unknown', hint: 'Probe returned empty response' }
    }

    const mapped = mapNodeStatus(probe.value, serviceName)
    if (mapped.status === 'not_supported' && !mapped.remediation && serviceName in ENABLE_HINTS) {
      mapped.remediation = ENABLE_HINTS[serviceName]
    }
    return {
      name: serviceName,
      ...mapped,
      detail: probe.value,
    }
  }

  const [aicfEntry, daEntry, minerEntry, quantumEntry] = await Promise.all([
    probeServiceStatus(caps.supportsAicfStatus, 'aicf.status', AICF_STATUS_METHODS, 'aicf'),
    probeServiceStatus(caps.supportsDaStatus, 'da.status', DA_STATUS_METHODS, 'da'),
    probeServiceStatus(caps.supportsMinerStatus, 'miner.status', MINER_STATUS_METHODS, 'miner'),
    probeServiceStatus(caps.supportsQuantumStatus, 'quantum.status', QUANTUM_STATUS_METHODS, 'quantum'),
  ])

  services.push(aicfEntry, daEntry, minerEntry, quantumEntry)

  // If admin/node status provides enriched data, merge it.
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
  workers?: unknown
  workJobs?: unknown
}

export async function getAICFInfo(rpc: RpcClient, address?: string): Promise<AICFInfo> {
  const [status, credits, jobs, plans, workers, workJobs] = await Promise.all([
    tryCall<unknown>(rpc, 'aicf.getStatus', []),
    address ? tryCall<unknown>(rpc, 'aicf.getCredits', [address]) : Promise.resolve(null),
    tryCall<unknown>(rpc, 'aicf.listJobs', [{ limit: 20 }]),
    tryCall<unknown>(rpc, 'aicf.listPlans', []),
    // The LIVE serving layer. The network runs AICF via the work layer + the
    // animica.dev gateway even when this node's credit-ledger state module is
    // not loaded (aicf.getStatus/summary report "unavailable" then). These are
    // the methods the node actually serves — probing only the ledger made the
    // explorer wrongly report "AICF not available on this node".
    tryCall<{ workers?: unknown[] }>(rpc, 'aicf.work.listWorkers', {}),
    tryCall<{ jobs?: unknown[] }>(rpc, 'aicf.work.listJobs', { limit: 20 }),
  ])

  const workersActive = !!workers && Array.isArray((workers as { workers?: unknown[] }).workers)
  const workJobsActive = !!workJobs && Array.isArray((workJobs as { jobs?: unknown[] }).jobs)

  // Available if EITHER the ledger layer OR the live work layer responds.
  const available =
    status !== null || jobs !== null || plans !== null || workersActive || workJobsActive

  return { available, status, credits, jobs: jobs ?? workJobs, plans, workers, workJobs }
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
