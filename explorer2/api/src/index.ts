import { config } from './config'
import { defaultChainDbPath, HybridChainClient, LocalChainClient } from './localChainClient'
import { RpcClient } from './rpcClient'
import { ExplorerService } from './service'
import { createServer } from './server'

const rpc = new RpcClient({ url: config.rpcUrl })
const chainDbPath = config.dbPath || defaultChainDbPath(config.chainId, config.dataRoot)
const local = new LocalChainClient(chainDbPath)
const chain = new HybridChainClient(local, rpc)

const service = new ExplorerService(chain, {
  head: config.cacheHeadTtlMs,
  blocks: config.cacheBlocksTtlMs,
  tx: config.cacheTxTtlMs
})

const app = createServer(service, config.corsOrigin, config.logLevel)

app.listen(config.port, () => {
  // eslint-disable-next-line no-console
  console.log(`Explorer2 API listening on ${config.port}`)
})
