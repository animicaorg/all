import { config } from './config.js'
import { defaultChainDbPath, HybridChainClient, LocalChainClient } from './localChainClient.js'
import { RpcClient } from './rpcClient.js'
import { RpcChainClient } from './rpcChainClient.js'
import { ExplorerService, ChainClient } from './service.js'
import { createServer } from './server.js'
import { ExplorerStore } from './explorerStore.js'
import { ContractVerifier } from './contractVerifier.js'
import { TokenTracker } from './tokensService.js'
import { extractDeployCode, extractTxInputData } from './txClassifier.js'
import pino from 'pino'
import { createHash } from 'node:crypto'

const log = pino({ name: 'explorer2-api', level: config.logLevel })

let chain: ChainClient
let mode: 'RPC' | 'Local DB' = 'RPC'
let detectedHead: number | null = null
let rpcClientRef: RpcClient | undefined
const runtimeStatus: {
  rpcReady: boolean | null
  lastRpcError: string | null
  lastRpcCheckAt: string | null
} = {
  rpcReady: null,
  lastRpcError: null,
  lastRpcCheckAt: null
}

function normalizeBytecode(code: unknown): string | null {
  if (typeof code !== 'string') return null
  const trimmed = code.trim().toLowerCase()
  if (!trimmed.length || trimmed === '0x') return null
  if (trimmed.startsWith('0x')) {
    const body = trimmed.slice(2)
    if (!body.length || /^0+$/.test(body) || /[^0-9a-f]/i.test(body)) return null
    return `0x${body}`
  }
  if (/^[0-9a-f]+$/i.test(trimmed) && !/^0+$/i.test(trimmed)) return `0x${trimmed}`
  return null
}

function decodeHex(value: string): Buffer | null {
  const normalized = normalizeBytecode(value)
  if (!normalized) return null
  const raw = normalized.slice(2)
  if (raw.length % 2 !== 0 || /[^0-9a-f]/i.test(raw)) return null
  return Buffer.from(raw, 'hex')
}

function hashHex(value: string): string {
  const decoded = decodeHex(value) ?? Buffer.from(value, 'utf-8')
  return `0x${createHash('sha3-256').update(decoded).digest('hex')}`
}

function extractConstructorArgs(rawTx: unknown): string | null {
  if (!rawTx || typeof rawTx !== 'object') return null
  const tx = rawTx as any
  const candidate =
    tx.constructorArgs ??
    tx.constructor_args ??
    tx.args ??
    tx.payload?.v?.constructorArgs ??
    tx.payload?.v?.constructor_args ??
    tx.tx?.payload?.v?.constructorArgs ??
    tx.tx?.payload?.v?.constructor_args
  if (typeof candidate !== 'string') return null
  const trimmed = candidate.trim()
  if (!trimmed.length) return null
  if (trimmed.startsWith('0x')) return trimmed.toLowerCase()
  if (/^[0-9a-f]+$/i.test(trimmed)) return `0x${trimmed.toLowerCase()}`
  return null
}

let refreshRpcState: (() => Promise<void>) | null = null

// Try RPC connection first
if (config.rpcUrl) {
  log.info({ rpcUrl: config.rpcUrl }, 'Attempting RPC connection...')

  const rpcClient = new RpcClient({
    url: config.rpcUrl,
    timeout: config.rpcTimeout,
    maxRetries: config.rpcMaxRetries
  })
  rpcClientRef = rpcClient

  const rpcChain = new RpcChainClient(rpcClient)

  mode = 'RPC'
  chain = rpcChain
  let capabilitiesDetected = false

  refreshRpcState = async () => {
    runtimeStatus.lastRpcCheckAt = new Date().toISOString()
    const rpcOk = await rpcClient.ping()
    if (!rpcOk) {
      runtimeStatus.rpcReady = false
      runtimeStatus.lastRpcError = 'RPC ping failed'
      return
    }

    runtimeStatus.rpcReady = true
    runtimeStatus.lastRpcError = null

    try {
      const head = await rpcChain.getHead() as any
      const resolvedHeight = head?.height ?? head?.number ?? null
      if (typeof resolvedHeight === 'number' && Number.isFinite(resolvedHeight)) {
        detectedHead = resolvedHeight
      }
    } catch (error) {
      runtimeStatus.lastRpcError = error instanceof Error ? error.message : String(error)
      log.warn({ err: error }, 'Could not detect head height from RPC')
    }

    if (!capabilitiesDetected) {
      try {
        await rpcChain.detectCapabilities()
        capabilitiesDetected = true
      } catch (error) {
        log.warn({ err: error }, 'Failed to detect capabilities')
      }
    }
  }

  await refreshRpcState()
  if (runtimeStatus.rpcReady) {
    log.info({ headHeight: detectedHead }, '✓ RPC connection established')
  } else {
    log.warn(
      { rpcUrl: config.rpcUrl, error: runtimeStatus.lastRpcError },
      'RPC is not ready yet; Explorer2 started in degraded RPC mode and will retry'
    )
  }
} else {
  log.warn('No RPC URL configured, using local database mode')
  mode = 'Local DB'

  const chainDbPath = config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)
  let local: LocalChainClient | null = null
  try {
    local = new LocalChainClient(chainDbPath)
    const head = await local.getHead() as any
    detectedHead = head?.height ?? null
    log.info({ chainDbPath, headHeight: detectedHead }, 'Local chain database loaded')
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`Local chain database unavailable:\n${message}`)
  }
  if (!local) {
    throw new Error('Local chain database unavailable.')
  }
  chain = new HybridChainClient(local)
  runtimeStatus.rpcReady = null
  runtimeStatus.lastRpcError = null
  runtimeStatus.lastRpcCheckAt = null
}

const store = new ExplorerStore({ dbPath: config.explorerIndexDbPath })
const verifier = new ContractVerifier(store, {
  resolveOnChainMaterial: async (address) => {
    const profile = store.getContractProfile(address)
    const runtimeCodeRaw = chain.getCode ? await chain.getCode(address).catch(() => null) : null
    const runtimeBytecode = normalizeBytecode(runtimeCodeRaw)
    const runtimeBytecodeHash = runtimeBytecode ? hashHex(runtimeBytecode) : profile?.runtimeCodeHash ?? null

    let creationBytecode: string | null = null
    let creationBytecodeHash: string | null = profile?.codeHash ?? null
    let constructorArgs: string | null = null
    if (profile?.creatorTxHash) {
      const tx = await chain.getTransactionByHash(profile.creatorTxHash).catch(() => null)
      if (tx) {
        creationBytecode = extractDeployCode(tx) ?? extractTxInputData(tx)
        if (creationBytecode) {
          creationBytecodeHash = hashHex(creationBytecode)
        }
        constructorArgs = extractConstructorArgs(tx)
      }
    }

    if (!runtimeBytecodeHash && !creationBytecodeHash) return null
    return {
      address,
      creationBytecode,
      creationBytecodeHash,
      runtimeBytecode,
      runtimeBytecodeHash,
      onChainCodeHash: runtimeBytecodeHash,
      constructorArgs
    }
  },
  resolveDefaultAbi: async (address) => store.getContractProfile(address)?.abi ?? null
})

const service = new ExplorerService(chain, {
  store,
  verifier
})

// Token tracker: background ANM-20 indexer (deploys + init calls + promos),
// served via /api/tokens*. Resilient by construction — a failed tick logs and
// retries on the next interval; it can never crash the server.
const tokenTracker = new TokenTracker({
  chain,
  store,
  rpc: rpcClientRef ?? null,
  dexFactory: config.dexFactoryAddress,
  scanDepth: config.tokenScanDepth,
  blocksPerTick: config.tokenScanBlocksPerTick
})
tokenTracker.start(config.tokenScanIntervalMs)

// Export diagnostics info for /api/diagnostics endpoint
export const diagnostics = {
  mode,
  rpcUrl: config.rpcUrl || null,
  chainDbPath: mode === 'Local DB' ? (config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)) : null,
  chainId: config.chainId,
  detectedHead,
  timestamp: new Date().toISOString(),
  runtimeStatus
}

const app = createServer(service, config.corsOrigin, config.logLevel, diagnostics, rpcClientRef, tokenTracker)

if (refreshRpcState) {
  const interval = setInterval(() => {
    void refreshRpcState!().catch((error) => {
      runtimeStatus.rpcReady = false
      runtimeStatus.lastRpcCheckAt = new Date().toISOString()
      runtimeStatus.lastRpcError = error instanceof Error ? error.message : String(error)
      log.warn({ err: error }, 'RPC readiness refresh failed')
    })
  }, 5_000)
  interval.unref()
}

app.listen(config.port, () => {
  log.info({ 
    port: config.port, 
    mode,
    rpcUrl: mode === 'RPC' ? config.rpcUrl : undefined,
    chainDbPath: mode === 'Local DB' ? diagnostics.chainDbPath : undefined,
    headHeight: detectedHead 
  }, `Explorer2 API started in ${mode} mode`)
})
