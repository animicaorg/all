import { config } from './config.js'
import { defaultChainDbPath, HybridChainClient, LocalChainClient } from './localChainClient.js'
import { RpcClient } from './rpcClient.js'
import { RpcChainClient } from './rpcChainClient.js'
import { ExplorerService, ChainClient } from './service.js'
import { createServer } from './server.js'
import pino from 'pino'
import fs from 'node:fs'

const log = pino({ name: 'explorer2-api', level: config.logLevel })

let chain: ChainClient
let mode: 'RPC' | 'Local DB' = 'RPC'
let detectedHead: number | null = null
let rpcClientRef: RpcClient | undefined

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
  
  // Test connectivity at startup
  const rpcOk = await rpcClient.ping()
  if (rpcOk) {
    log.info('✓ RPC connection established')
    mode = 'RPC'
    chain = rpcChain
    
    // Detect head height
    try {
      const head = await rpcChain.getHead() as any
      detectedHead = head?.height ?? null
      log.info({ headHeight: detectedHead }, '✓ RPC head detected')
    } catch (err) {
      log.warn({ err }, 'Could not detect head height from RPC')
    }
    
    // Detect capabilities in background
    rpcChain.detectCapabilities().catch(err => {
      log.warn({ err }, 'Failed to detect capabilities')
    })
  } else {
    log.warn('✗ RPC connection failed, falling back to local database')
    mode = 'Local DB'
    
    const chainDbPath = config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)
    let local: LocalChainClient | null = null
    try {
      // Check if DB exists before trying to open
      if (!fs.existsSync(chainDbPath)) {
        throw new Error(`Database file not found at ${chainDbPath}`)
      }
      
      local = new LocalChainClient(chainDbPath)
      
      // Try to get head to verify DB is valid
      const head = await local.getHead() as any
      detectedHead = head?.height ?? null
      
      log.info({ chainDbPath, headHeight: detectedHead }, '✓ Local chain database loaded')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      throw new Error(`Both RPC and local DB unavailable:\nRPC: ${config.rpcUrl} (unreachable)\nLocal DB: ${message}`)
    }
    if (!local) {
      throw new Error('Local chain database unavailable.')
    }
    chain = new HybridChainClient(local)
  }
} else {
  // Should not reach here with default config, but keep as fallback
  log.warn('No RPC URL configured (unexpected)')
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
}

const service = new ExplorerService(chain)

// Export diagnostics info for /api/diagnostics endpoint
export const diagnostics = {
  mode,
  rpcUrl: config.rpcUrl || null,
  chainDbPath: mode === 'Local DB' ? (config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)) : null,
  chainId: config.chainId,
  detectedHead,
  timestamp: new Date().toISOString()
}

const app = createServer(service, config.corsOrigin, config.logLevel, diagnostics, rpcClientRef)

app.listen(config.port, () => {
  log.info({ 
    port: config.port, 
    mode,
    rpcUrl: mode === 'RPC' ? config.rpcUrl : undefined,
    chainDbPath: mode === 'Local DB' ? diagnostics.chainDbPath : undefined,
    headHeight: detectedHead 
  }, `Explorer2 API started in ${mode} mode`)
})
