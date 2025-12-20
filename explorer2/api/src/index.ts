import { config } from './config'
import { RpcClient } from './rpcClient'
import { ExplorerService } from './service'
import { createServer } from './server'

const rpc = new RpcClient({ url: config.rpcUrl })
const service = new ExplorerService(rpc, {
  head: config.cacheHeadTtlMs,
  blocks: config.cacheBlocksTtlMs,
  tx: config.cacheTxTtlMs
})

const app = createServer(service, config.corsOrigin, config.logLevel)

app.listen(config.port, () => {
  // eslint-disable-next-line no-console
  console.log(`Explorer2 API listening on ${config.port}`)
})
