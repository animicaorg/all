import { config } from './config.js'
import { RpcClient } from './rpcClient.js'
import { RpcChainClient } from './rpcChainClient.js'
import { ExplorerService } from './service.js'

async function main() {
  const limitArg = process.argv.find((arg) => arg.startsWith('--limit='))
  const limit = limitArg ? Number(limitArg.split('=')[1]) : 100

  if (!config.rpcUrl) {
    throw new Error('RPC URL is required for backfill command')
  }

  const rpcClient = new RpcClient({
    url: config.rpcUrl,
    timeout: config.rpcTimeout,
    maxRetries: config.rpcMaxRetries
  })

  const rpcOk = await rpcClient.ping()
  if (!rpcOk) {
    throw new Error(`RPC unavailable: ${config.rpcUrl}`)
  }

  const service = new ExplorerService(new RpcChainClient(rpcClient))
  const result = await service.backfillConfirmedTxsMissingFields(limit)
  console.log(JSON.stringify({ command: 'explorer2 backfill confirmed-txs --missing-fields', ...result }))
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
