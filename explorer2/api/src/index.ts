import { config } from './config'
import { defaultChainDbPath, HybridChainClient, LocalChainClient } from './localChainClient'
import { ExplorerService } from './service'
import { createServer } from './server'

const chainDbPath = config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)
let local: LocalChainClient | null = null
try {
  local = new LocalChainClient(chainDbPath)
} catch (error) {
  const message = error instanceof Error ? error.message : String(error)
  throw new Error(`Local chain database unavailable:\n${message}`)
}
if (!local) {
  throw new Error('Local chain database unavailable.')
}
const chain = new HybridChainClient(local)

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
  // eslint-disable-next-line no-console
  console.log(`Explorer2 API listening on ${config.port}`)
})
