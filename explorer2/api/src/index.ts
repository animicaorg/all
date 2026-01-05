import { config } from './config'
import { defaultChainDbPath, HybridChainClient, LocalChainClient } from './localChainClient'
import { RpcClient } from './rpcClient'
import { RpcChainClient } from './rpcChainClient'
import { ExplorerService, ChainClient } from './service'
import { createServer } from './server'
import pino from 'pino'

const log = pino({ name: 'explorer2-api', level: config.logLevel })

let chain: ChainClient

// Prioritize RPC connection if configured
if (config.rpcUrl) {
  log.info({ rpcUrl: config.rpcUrl }, 'Using RPC client to connect to node')
  
  const rpcClient = new RpcClient({
    url: config.rpcUrl,
    timeout: config.rpcTimeout,
    maxRetries: config.rpcMaxRetries
  })
  
  const rpcChain = new RpcChainClient(rpcClient)
  
  // Test connectivity at startup
  rpcClient.ping().then(ok => {
    if (ok) {
      log.info('RPC connection established')
      rpcChain.detectCapabilities().catch(err => {
        log.warn({ err }, 'Failed to detect capabilities')
      })
    } else {
      log.error('RPC connection failed - explorer may not work correctly')
    }
  })
  
  chain = rpcChain
} else {
  // Fallback to local database
  log.info('No RPC URL configured, using local database')
  
  const chainDbPath = config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)
  let local: LocalChainClient | null = null
  try {
    local = new LocalChainClient(chainDbPath)
    log.info({ chainDbPath }, 'Local chain database loaded')
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    throw new Error(`Local chain database unavailable:\n${message}`)
  }
  if (!local) {
    throw new Error('Local chain database unavailable.')
  }
  chain = new HybridChainClient(local)
}

const service = new ExplorerService(
  chain,
  {
    head: config.cacheHeadTtlMs,
    blocks: config.cacheBlocksTtlMs,
    tx: config.cacheTxTtlMs
  },
  { persistPath: config.cachePersistPath || undefined }
)

const app = createServer(service, config.corsOrigin, config.logLevel)

app.listen(config.port, () => {
  log.info({ port: config.port, mode: config.rpcUrl ? 'RPC' : 'Local DB' }, 'Explorer2 API started')
})
