import express from 'express'
import cors from 'cors'
import pino from 'pino'
import pinoHttp from 'pino-http'
import type { ApiError } from '@animica/explorer2-shared'
import { ExplorerService } from './service.js'
import { HttpError } from './errors.js'
import { rpcDiscover, getServiceStatus, getAICFInfo, getMiningInfo, getDAInfo, daGetBlob, daGetProof, daPutBlob, daListHistory, getQuantumInfo } from './rpcExtended.js'
import { RpcClient, RpcError } from './rpcClient.js'
import { bigIntSafeStringify } from './bigIntJson.js'
import fs from 'node:fs'

interface DiagnosticsInfo {
  mode: string
  rpcUrl: string | null
  chainDbPath: string | null
  chainId: number
  detectedHead: number | null
  timestamp: string
  runtimeStatus?: {
    rpcReady: boolean | null
    lastRpcError: string | null
    lastRpcCheckAt: string | null
  }
}

export function createServer(service: ExplorerService, corsOrigin: string, logLevel: string, diagnostics?: DiagnosticsInfo, rpc?: RpcClient) {
  const app = express()
  const logger = pino({ level: logLevel })

  app.use(cors({ origin: corsOrigin }))
  app.use(express.json({ limit: '1mb' }))
  app.use(pinoHttp({ logger: logger as any }))

  // BigInt-safe JSON responder helper
  const jsonSafe = (res: express.Response, data: unknown, status = 200) => {
    res.status(status).type('application/json').send(bigIntSafeStringify(data))
  }

  app.get('/api/health', async (_req, res) => {
    const rpcReady = diagnostics?.runtimeStatus?.rpcReady
    res.json({
      ok: true,
      ready: rpcReady === null ? true : rpcReady,
      mode: diagnostics?.mode ?? 'Unknown',
      timestamp: new Date().toISOString()
    })
  })

  app.get('/api/meta', async (_req, res) => {
    res.json({
      explorer: {
        name: 'Animica Explorer',
        version: '0.1.0',
        mode: diagnostics?.mode || 'Unknown'
      },
      network: {
        chainId: diagnostics?.chainId || null,
        rpcUrl: diagnostics?.rpcUrl || null
      },
      timestamp: new Date().toISOString()
    })
  })

  app.get('/api/diagnostics', async (_req, res) => {
    try {
      interface HeadData {
        head?: {
          height: number
          hash: string
          time: number
        }
      }
      let currentHead: HeadData['head'] | null = null
      try {
        const headData = await service.getHead() as HeadData
        currentHead = headData?.head ?? null
      } catch {
        // Ignore errors, diagnostics should still work
      }

      const dbPath = diagnostics?.chainDbPath
      let dbExists = false
      let dbSizeBytes: number | null = null
      let dbLastModified: string | null = null

      if (dbPath && typeof dbPath === 'string') {
        try {
          const stats = fs.statSync(dbPath)
          dbExists = true
          dbSizeBytes = stats.size
          dbLastModified = stats.mtime.toISOString()
        } catch {
          // DB doesn't exist or not accessible
        }
      }

      res.json({
        mode: diagnostics?.mode || 'Unknown',
        rpcUrl: diagnostics?.rpcUrl || null,
        chainDbPath: dbPath || null,
        chainId: diagnostics?.chainId || null,
        detectedHead: diagnostics?.detectedHead || null,
        currentHead: currentHead ? {
          height: currentHead.height,
          hash: currentHead.hash,
          time: currentHead.time
        } : null,
        database: {
          exists: dbExists,
          sizeBytes: dbSizeBytes,
          lastModified: dbLastModified
        },
        startupTime: diagnostics?.timestamp || null,
        runtimeStatus: diagnostics?.runtimeStatus ?? null,
        currentTime: new Date().toISOString()
      })
    } catch (err) {
      logger.error({ err }, 'Diagnostics endpoint failed')
      res.status(500).json({
        error: 'diagnostics_failed',
        message: err instanceof Error ? err.message : String(err)
      })
    }
  })

  app.get('/api/head', async (_req, res, next) => {
    try {
      const payload = await service.getHead()
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/blocks', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 20)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getBlocks(limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/block/:hashOrHeight', async (req, res, next) => {
    try {
      const payload = await service.getBlockDetail(req.params.hashOrHeight)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/tx/:hash', async (req, res, next) => {
    try {
      const payload = await service.getTxDetail(req.params.hash)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/address/:bech32', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 20)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getAddressDetail(req.params.bech32, limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/mempool', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 50)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getMempool(limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/deployments', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 24)
      const scanBlocks = Number(req.query.scanBlocks || 240)
      const payload = await service.getContractDeployments(limit, scanBlocks)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/address/:address', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 20)
      const cursor = typeof req.query.cursor === 'string' ? req.query.cursor : undefined
      const payload = await service.getContractDetail(req.params.address, limit, cursor)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/address/:address/code', async (req, res, next) => {
    try {
      const payload = await service.getContractCode(req.params.address)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/address/:address/verification', async (req, res, next) => {
    try {
      const payload = service.getContractVerification(req.params.address)
      if (!payload) {
        res.json({
          status: 'unverified',
          verifierStatus: 'unverified',
          submittedAt: null,
          completedAt: null
        })
        return
      }
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/created-by/:txHash', async (req, res, next) => {
    try {
      const payload = await service.getContractByCreationTx(req.params.txHash)
      if (!payload) {
        res.status(404).json({ error: 'not_found', message: 'No contract found for transaction hash' })
        return
      }
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.post('/api/contracts/verify', async (req, res, next) => {
    try {
      const payload = await service.submitContractVerification(req.body ?? {})
      res.status(202).json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/contracts/verify/:jobId', async (req, res, next) => {
    try {
      const payload = service.getContractVerificationJob(req.params.jobId)
      if (!payload) {
        res.status(404).json({ error: 'not_found', message: 'Verification job not found' })
        return
      }
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/search', async (req, res, next) => {
    try {
      const query = typeof req.query.q === 'string' ? req.query.q : ''
      const payload = await service.search(query)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/richlist', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || 100)
      const offset = Number(req.query.offset || 0)
      const payload = await service.getRichList(limit, offset)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  app.get('/api/richlist/summary', async (_req, res, next) => {
    try {
      const payload = await service.getRichListSummary()
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })


  app.post('/api/admin/backfill/confirmed-txs', async (req, res, next) => {
    try {
      const limit = Number(req.query.limit || req.body?.limit || 100)
      const payload = await service.backfillConfirmedTxsMissingFields(limit)
      res.json(payload)
    } catch (err) {
      next(err)
    }
  })

  // ── Network Health / Service Status ────────────────────────────────────────

  app.get('/api/network/status', async (_req, res) => {
    if (!rpc) {
      res.json({ timestamp: new Date().toISOString(), services: [], note: 'Service status requires RPC mode' })
      return
    }
    try {
      const status = await getServiceStatus(rpc)
      jsonSafe(res, status)
    } catch (err) {
      res.status(500).json({ error: 'status_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── RPC Inspector ───────────────────────────────────────────────────────────

  app.get('/api/rpc/discover', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false, methods: [], note: 'RPC inspector requires RPC mode' })
      return
    }
    try {
      const result = await rpcDiscover(rpc)
      jsonSafe(res, result)
    } catch (err) {
      res.status(500).json({ error: 'discover_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── AICF ────────────────────────────────────────────────────────────────────

  app.get('/api/aicf/info', async (req, res) => {
    if (!rpc) {
      res.json({ available: false })
      return
    }
    const address = typeof req.query.address === 'string' ? req.query.address : undefined
    try {
      const info = await getAICFInfo(rpc, address)
      jsonSafe(res, info)
    } catch (err) {
      res.status(500).json({ error: 'aicf_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/aicf/summary', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false, total_minted: '0', total_spent: '0', balance: '0', event_count: 0, recent_events: [] })
      return
    }
    try {
      const result = await rpc.call('aicf.summary', [])
      jsonSafe(res, result ?? { available: false })
    } catch (err) {
      res.status(500).json({ error: 'aicf_summary_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/aicf/events', async (req, res) => {
    if (!rpc) {
      res.json({ available: false, events: [] })
      return
    }
    const limit = typeof req.query.limit === 'string' ? parseInt(req.query.limit, 10) || 20 : 20
    try {
      const result = await rpc.call('aicf.recentEvents', [limit])
      jsonSafe(res, result ?? { events: [] })
    } catch (err) {
      res.status(500).json({ error: 'aicf_events_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/aicf/address/:addr', async (req, res) => {
    if (!rpc) {
      res.json({ available: false, address: req.params.addr, balance: '0', events: [] })
      return
    }
    const { addr } = req.params
    try {
      const fallback = { available: false, address: addr, balance: '0', spent: '0', minted: '0', events: [] as unknown[] }
      const attempts: Array<unknown[] | Record<string, unknown> | string> = [[addr], { address: addr }, addr]

      let lastError: unknown = null
      for (const params of attempts) {
        try {
          const result = await rpc.call('aicf.creditsByAddress', params as any)
          const payload = result && typeof result === 'object' ? result as Record<string, unknown> : {}
          jsonSafe(res, { available: true, ...payload })
          return
        } catch (error) {
          lastError = error
          if (isRpcMethodNotFound(error) || isRpcInvalidParams(error)) {
            continue
          }
          if (isRpcUnavailable(error)) {
            logger.warn({ err: error, address: addr }, 'AICF credits endpoint unavailable; returning empty state')
            jsonSafe(res, { ...fallback, reason: errorMessage(error) })
            return
          }
          break
        }
      }

      const info = await getAICFInfo(rpc, addr)
      if (info.credits && typeof info.credits === 'object') {
        jsonSafe(res, { available: info.available, ...(info.credits as object) })
        return
      }
      if (info.available) {
        jsonSafe(res, { ...fallback, available: true })
        return
      }

      if (lastError && !isRpcMethodNotFound(lastError) && !isRpcInvalidParams(lastError) && !isRpcUnavailable(lastError)) {
        throw lastError
      }
      jsonSafe(res, fallback)
    } catch (err) {
      logger.error({ err, address: addr }, 'AICF address endpoint failed')
      res.status(500).json({ error: 'aicf_address_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── Mining ──────────────────────────────────────────────────────────────────

  app.get('/api/mining/info', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false })
      return
    }
    try {
      const info = await getMiningInfo(rpc)
      jsonSafe(res, info)
    } catch (err) {
      res.status(500).json({ error: 'mining_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── DA ──────────────────────────────────────────────────────────────────────

  app.get('/api/da/info', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false })
      return
    }
    try {
      const info = await getDAInfo(rpc)
      jsonSafe(res, info)
    } catch (err) {
      res.status(500).json({ error: 'da_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/blob/:commitment', async (req, res) => {
    if (!rpc) {
      res.status(503).json({ error: 'rpc_required', message: 'DA requires RPC mode' })
      return
    }
    try {
      const blob = await daGetBlob(rpc, req.params.commitment)
      if (blob === null) {
        res.status(404).json({ error: 'not_found', message: 'Blob not found or DA not available on this node' })
        return
      }
      jsonSafe(res, blob)
    } catch (err) {
      res.status(500).json({ error: 'da_get_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/proof/:commitment', async (req, res) => {
    if (!rpc) {
      res.status(503).json({ error: 'rpc_required', message: 'DA requires RPC mode' })
      return
    }
    try {
      const proof = await daGetProof(rpc, req.params.commitment)
      if (proof === null) {
        res.status(404).json({ error: 'not_found', message: 'Proof not found or DA not available on this node' })
        return
      }
      jsonSafe(res, proof)
    } catch (err) {
      res.status(500).json({ error: 'da_proof_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/history', async (req, res) => {
    if (!rpc) {
      res.json([])
      return
    }
    try {
      const limit = Math.min(Number(req.query.limit || 20), 100)
      const history = await daListHistory(rpc, limit)
      jsonSafe(res, history)
    } catch (err) {
      res.status(500).json({ error: 'da_history_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.post('/api/da/put', async (req, res) => {
    if (!rpc) {
      res.status(503).json({ error: 'rpc_required', message: 'DA requires RPC mode' })
      return
    }
    const { namespace, data } = req.body || {}
    if (typeof namespace !== 'string' || typeof data !== 'string') {
      res.status(400).json({ error: 'bad_request', message: 'namespace and data (base64 string) are required' })
      return
    }
    try {
      const result = await daPutBlob(rpc, namespace, data)
      if (result === null) {
        res.status(503).json({ error: 'not_available', message: 'da.putBlob not available on this node' })
        return
      }
      jsonSafe(res, result)
    } catch (err) {
      res.status(500).json({ error: 'da_put_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/status', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false })
      return
    }
    try {
      const result = await rpc.call('da.status', [])
      jsonSafe(res, result ?? { available: false })
    } catch (err) {
      res.status(500).json({ error: 'da_status_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/recent', async (req, res) => {
    if (!rpc) {
      res.json({ available: false, blobs: [] })
      return
    }
    const limit = typeof req.query.limit === 'string' ? parseInt(req.query.limit, 10) || 20 : 20
    try {
      const result = await rpc.call('da.list', [{ limit }])
      jsonSafe(res, normalizeDaRecent(result))
    } catch (err) {
      if (isRpcUnavailable(err) || isRpcMethodNotFound(err)) {
        logger.warn({ err }, 'DA list unavailable; returning empty recent list')
        jsonSafe(res, { available: false, blobs: [] })
        return
      }
      logger.error({ err }, 'DA recent endpoint failed')
      res.status(500).json({ error: 'da_recent_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/da/blob/:id', async (req, res) => {
    if (!rpc) {
      res.status(503).json({ error: 'rpc_required', message: 'DA requires RPC mode' })
      return
    }
    const { id } = req.params
    try {
      // Return metadata only (no raw bytes)
      const result = await rpc.call('da.has', [id])
      if (!result) {
        res.status(404).json({ error: 'not_found', blob_id: id })
        return
      }
      jsonSafe(res, { blob_id: id, exists: true })
    } catch (err) {
      res.status(500).json({ error: 'da_blob_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── ENA ──────────────────────────────────────────────────────────────────────

  app.get('/api/ena/jobs', async (req, res) => {
    if (!rpc) {
      res.json({ available: false, jobs: [] })
      return
    }
    const limit = typeof req.query.limit === 'string' ? parseInt(req.query.limit, 10) || 20 : 20
    try {
      // Try ena.listModels as a lightweight proxy for job activity
      const result = await rpc.call('ena.listModels', []) as { models?: unknown[] } | null
      jsonSafe(res, { jobs: [], models: result?.models ?? [], limit })
    } catch (err) {
      res.status(500).json({ error: 'ena_jobs_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/ena/artifacts', async (req, res) => {
    if (!rpc) {
      res.json({ available: false, artifacts: [] })
      return
    }
    const limit = typeof req.query.limit === 'string' ? parseInt(req.query.limit, 10) || 20 : 20
    try {
      const result = await rpc.call('ena.listArtifacts', [limit])
      jsonSafe(res, result ?? { artifacts: [] })
    } catch (err) {
      res.status(500).json({ error: 'ena_artifacts_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/ena/job/:id', async (req, res) => {
    if (!rpc) {
      res.status(503).json({ error: 'rpc_required', message: 'ENA requires RPC mode' })
      return
    }
    const { id } = req.params
    try {
      const result = await rpc.call('ena.getRequest', [id])
      if (!result) {
        res.status(404).json({ error: 'not_found', job_id: id })
        return
      }
      jsonSafe(res, result)
    } catch (err) {
      res.status(500).json({ error: 'ena_job_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── Quantum ─────────────────────────────────────────────────────────────────

  app.get('/api/quantum/info', async (_req, res) => {
    if (!rpc) {
      res.json({ available: false })
      return
    }
    try {
      const info = await getQuantumInfo(rpc)
      jsonSafe(res, info)
    } catch (err) {
      res.status(500).json({ error: 'quantum_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  // ── Debug Bundle ────────────────────────────────────────────────────────────

  app.get('/api/debug/bundle', async (_req, res) => {
    try {
      let headData: unknown = null
      try { headData = await service.getHead() } catch { /* ignore */ }

      let discoverData: unknown = null
      let statusData: unknown = null
      if (rpc) {
        try { discoverData = await rpcDiscover(rpc) } catch { /* ignore */ }
        try { statusData = await getServiceStatus(rpc) } catch { /* ignore */ }
      }

      const bundle = {
        exportedAt: new Date().toISOString(),
        explorerVersion: '0.1.0',
        profile: {
          mode: diagnostics?.mode || 'Unknown',
          chainId: diagnostics?.chainId || null,
          rpcUrl: diagnostics?.rpcUrl ? redactUrl(diagnostics.rpcUrl) : null,
        },
        rpcDiscover: discoverData,
        serviceStatus: statusData,
        currentHead: headData,
        recentErrors: [],
      }
      jsonSafe(res, bundle)
    } catch (err) {
      res.status(500).json({ error: 'bundle_failed', message: err instanceof Error ? err.message : String(err) })
    }
  })

  app.get('/api/debug/rpc', async (_req, res) => {
    if (process.env.NODE_ENV === 'production') {
      res.status(404).json({ error: 'not_found', message: 'Debug endpoints disabled in production' })
      return
    }

    res.json({
      mode: diagnostics?.mode || 'Unknown',
      rpcUrl: diagnostics?.rpcUrl || null,
      timeout: 30000,
      maxRetries: 3,
      timestamp: new Date().toISOString()
    })
  })

  app.use((err: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    if (err instanceof HttpError) {
      logger.warn({ err, path: _req.originalUrl, method: _req.method }, 'Handled API error')
      const body: ApiError = { error: 'request_failed', message: err.message, detail: err.detail }
      res.status(err.status).json(body)
      return
    }
    logger.error({ err, path: _req.originalUrl, method: _req.method }, 'Unhandled API error')
    const message = err instanceof Error ? err.message : 'Unexpected error'
    res.status(500).json({ error: 'unexpected_error', message })
  })

  return app
}

/** Redact credentials from URLs (user:pass@ patterns). */
function redactUrl(url: string): string {
  try {
    const u = new URL(url)
    if (u.password) u.password = '***'
    if (u.username) u.username = '***'
    return u.toString()
  } catch {
    return url
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

function isRpcMethodNotFound(err: unknown): boolean {
  const message = errorMessage(err).toLowerCase()
  return (
    message.includes('method not found') ||
    message.includes('unknown method') ||
    message.includes('not implemented') ||
    (err instanceof RpcError && err.code === -32601)
  )
}

function isRpcInvalidParams(err: unknown): boolean {
  const message = errorMessage(err).toLowerCase()
  return (
    message.includes('invalid params') ||
    message.includes('missing required parameter') ||
    message.includes('unexpected keyword argument') ||
    message.includes('params must') ||
    (err instanceof RpcError && err.code === -32602)
  )
}

function isRpcUnavailable(err: unknown): boolean {
  const message = errorMessage(err).toLowerCase()
  return (
    message.includes('not enabled') ||
    message.includes('disabled') ||
    message.includes('temporarily unavailable') ||
    message.includes('not configured') ||
    message.includes('service unavailable') ||
    message.includes('rpc unavailable')
  )
}

function normalizeDaRecent(result: unknown): { available: boolean; blobs: unknown[]; nextCursor?: string | null } {
  if (Array.isArray(result)) {
    return { available: true, blobs: result }
  }
  if (result && typeof result === 'object') {
    const record = result as Record<string, unknown>
    if (Array.isArray(record.items)) {
      return {
        available: true,
        blobs: record.items,
        nextCursor: typeof record.next_cursor === 'string' ? record.next_cursor : null
      }
    }
    if (Array.isArray(record.blobs)) {
      return {
        available: true,
        blobs: record.blobs,
        nextCursor: typeof record.nextCursor === 'string' ? record.nextCursor : null
      }
    }
  }
  return { available: false, blobs: [] }
}
