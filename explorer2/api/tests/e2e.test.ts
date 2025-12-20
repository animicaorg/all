import http from 'node:http'
import type { AddressInfo } from 'node:net'
import request from 'supertest'
import { describe, expect, it, beforeAll, afterAll } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'
import { RpcClient } from '../src/rpcClient'

function createMockRpcServer() {
  const server = http.createServer((req, res) => {
    let body = ''
    req.on('data', (chunk) => {
      body += chunk
    })
    req.on('end', () => {
      const payload = JSON.parse(body)
      const respond = (id: number | string | null, result: unknown) => ({ jsonrpc: '2.0', id, result })
      const handle = (call: any) => {
        const method = call.method
        if (method === 'chain.getHead') {
          return respond(call.id, { chainId: 1, height: 10, hash: '0xabc', time: 1000 })
        }
        if (method === 'chain.getBlockByNumber' || method === 'chain.getBlockByHash') {
          return respond(call.id, {
            header: { height: 10, hash: '0xabc', parentHash: '0xdef', time: 1000 },
            txs: [{ hash: '0xtx1', from: 'anim1from', to: 'anim1to', value: '0x1' }]
          })
        }
        if (method === 'tx.getTransactionByHash') {
          return respond(call.id, { hash: '0xtx1', from: 'anim1from', to: 'anim1to', value: '0x1' })
        }
        if (method === 'tx.getTransactionReceipt') {
          return respond(call.id, { txHash: '0xtx1', blockHash: '0xabc', blockNumber: 10, status: 'SUCCESS', gasUsed: '0x10', logs: [] })
        }
        if (method === 'mempool.getPending') {
          return respond(call.id, ['0xtx2'])
        }
        if (method === 'mempool.getStats') {
          return respond(call.id, { count: 1, totalBytes: 120, oldestAgeSec: 3 })
        }
        if (method === 'net.peers') {
          return respond(call.id, [{ direction: 'inbound' }, { direction: 'outbound' }])
        }
        if (method === 'state.getBalance') {
          return respond(call.id, '0x5')
        }
        return { jsonrpc: '2.0', id: call.id, error: { code: -32601, message: 'Method not found' } }
      }

      const response = Array.isArray(payload) ? payload.map(handle) : handle(payload)
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(response))
    })
  })

  return server
}

describe('Explorer API e2e', () => {
  let rpcServer: http.Server
  let api: ReturnType<typeof createServer>
  let rpcUrl = ''

  beforeAll(async () => {
    rpcServer = createMockRpcServer()
    await new Promise<void>((resolve) => rpcServer.listen(0, resolve))
    const { port } = rpcServer.address() as AddressInfo
    rpcUrl = `http://127.0.0.1:${port}`

    const service = new ExplorerService(new RpcClient({ url: rpcUrl }), { head: 1000, blocks: 1000, tx: 1000 })
    api = createServer(service, '*', 'silent')
  })

  afterAll(async () => {
    await new Promise<void>((resolve) => rpcServer.close(() => resolve()))
  })

  it('serves head stats', async () => {
    const res = await request(api).get('/api/head')
    expect(res.status).toBe(200)
    expect(res.body.head.height).toBe(10)
    expect(res.body.stats.peerCount).toBe(2)
  })

  it('serves block detail', async () => {
    const res = await request(api).get('/api/block/10')
    expect(res.status).toBe(200)
    expect(res.body.hash).toBe('0xabc')
  })

  it('serves tx detail', async () => {
    const res = await request(api).get('/api/tx/0xtx1')
    expect(res.status).toBe(200)
    expect(res.body.status).toBe('confirmed')
  })
})
