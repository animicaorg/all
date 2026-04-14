import request from 'supertest'
import { beforeAll, describe, expect, it } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'

const KNOWN_PENDING_TX = '0x' + '1'.repeat(64)
const UNKNOWN_TX = '0x' + '2'.repeat(64)
const TEST_ADDR = 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4'

function makeGenesisChain() {
  return {
    getHead: async () => ({ height: 0, number: 0, hash: '0xgenesis', time: 0 }),
    getBlockByNumber: async (height: number | string) => {
      const n = Number(height)
      if (n !== 0) throw new Error('Block not found')
      return { header: { number: 0, hash: '0xgenesis', parentHash: '0x0', time: 0 }, transactions: [] }
    },
    getBlockByHash: async () => ({ header: { number: 0, hash: '0xgenesis', parentHash: '0x0', time: 0 }, transactions: [] }),
    getTransactionByHash: async (hash: string) => {
      if (hash !== KNOWN_PENDING_TX) return null
      return { hash, from: TEST_ADDR, to: TEST_ADDR, value: '0x0' }
    },
    getTransactionReceipt: async (_hash: string) => null,
    getMempoolPending: async () => [KNOWN_PENDING_TX],
    getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
    getPeers: async () => [],
    getBalance: async () => '0x0',
    getRichList: async (_limit: number, _offset: number) => ({ height: 0, items: [], totalAddresses: 0 }),
    getTotalSupply: async () => ({ height: 0, totalSupply: '0x0', addressCount: 0 })
  }
}

describe('Explorer API resilience', () => {
  let api: ReturnType<typeof createServer>

  beforeAll(() => {
    const service = new ExplorerService(makeGenesisChain())
    api = createServer(service, '*', 'silent', {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 0,
      timestamp: new Date().toISOString(),
      runtimeStatus: {
        rpcReady: true,
        lastRpcError: null,
        lastRpcCheckAt: new Date().toISOString()
      }
    })
  })

  it('serves home/dashboard data on genesis-only chain', async () => {
    const headRes = await request(api).get('/api/head')
    expect(headRes.status).toBe(200)
    expect(headRes.body.head.height).toBe(0)

    const blocksRes = await request(api).get('/api/blocks?limit=5')
    expect(blocksRes.status).toBe(200)
    expect(Array.isArray(blocksRes.body.items)).toBe(true)
    expect(blocksRes.body.items).toHaveLength(1)
    expect(blocksRes.body.items[0].height).toBe(0)
  })

  it('serves block detail with minimal block shape', async () => {
    const res = await request(api).get('/api/block/0')
    expect(res.status).toBe(200)
    expect(res.body.height).toBe(0)
    expect(Array.isArray(res.body.txs)).toBe(true)
    expect(res.body.txs).toHaveLength(0)
  })

  it('handles tx pending and missing paths without 500', async () => {
    const pending = await request(api).get(`/api/tx/${KNOWN_PENDING_TX}`)
    expect(pending.status).toBe(200)
    expect(pending.body.status).toBe('pending')

    const missing = await request(api).get(`/api/tx/${UNKNOWN_TX}`)
    expect(missing.status).toBe(404)
    expect(missing.body.error).toBe('request_failed')
  })

  it('handles address page with zero balance and no history', async () => {
    const noHistoryService = new ExplorerService({
      ...makeGenesisChain(),
      getMempoolPending: async () => []
    })
    const noHistoryApi = createServer(noHistoryService, '*', 'silent')

    const res = await request(noHistoryApi).get(`/api/address/${TEST_ADDR}`)
    expect(res.status).toBe(200)
    expect(res.body.confirmedBalance).toBe('0x0')
    expect(res.body.pendingBalance).toBe('0x0')
    expect(Array.isArray(res.body.txs)).toBe(true)
    expect(res.body.txs).toHaveLength(0)
  })

  it('handles empty mempool', async () => {
    const emptyMempoolService = new ExplorerService({
      ...makeGenesisChain(),
      getMempoolPending: async () => []
    })
    const emptyMempoolApi = createServer(emptyMempoolService, '*', 'silent')

    const res = await request(emptyMempoolApi).get('/api/mempool')
    expect(res.status).toBe(200)
    expect(res.body.total).toBe(0)
    expect(Array.isArray(res.body.entries)).toBe(true)
    expect(res.body.entries).toHaveLength(0)
  })

  it('skips missing partial-sync blocks instead of failing the whole page', async () => {
    const service = new ExplorerService({
      ...makeGenesisChain(),
      getHead: async () => ({ height: 3, hash: '0xhead', time: 3 }),
      getBlockByNumber: async (height: number | string) => {
        const n = Number(height)
        if (n === 2) throw new Error('unknown block')
        return { header: { height: n, hash: `0x${n}`, parentHash: '0x0', time: n }, txs: [] }
      }
    })
    const partialApi = createServer(service, '*', 'silent')

    const res = await request(partialApi).get('/api/blocks?limit=4')
    expect(res.status).toBe(200)
    expect(Array.isArray(res.body.items)).toBe(true)
    expect(res.body.items.map((b: { height: number }) => b.height)).toEqual([3, 1, 0])
  })
})

describe('Startup/readiness and optional-feature hardening', () => {
  it('reports degraded readiness and returns 503 for RPC-unavailable core data', async () => {
    const failingService = new ExplorerService({
      getHead: async () => {
        throw new Error('connect ECONNREFUSED 127.0.0.1:8545')
      },
      getBlockByNumber: async () => {
        throw new Error('connect ECONNREFUSED 127.0.0.1:8545')
      },
      getBlockByHash: async () => {
        throw new Error('connect ECONNREFUSED 127.0.0.1:8545')
      },
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const api = createServer(failingService, '*', 'silent', {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: null,
      timestamp: new Date().toISOString(),
      runtimeStatus: {
        rpcReady: false,
        lastRpcError: 'RPC ping failed',
        lastRpcCheckAt: new Date().toISOString()
      }
    })

    const health = await request(api).get('/api/health')
    expect(health.status).toBe(200)
    expect(health.body.ready).toBe(false)

    const head = await request(api).get('/api/head')
    expect(head.status).toBe(503)
    expect(head.body.error).toBe('request_failed')
  })

  it('avoids 500 on AICF/DA optional endpoints when node reports disabled or shape mismatch', async () => {
    const baseService = new ExplorerService(makeGenesisChain())
    const rpcMock = {
      async call(method: string, _params?: unknown): Promise<unknown> {
        if (method === 'aicf.creditsByAddress') {
          const err = new Error('params must be a dict, list, or string address') as Error & { code?: number }
          err.code = -32602
          throw err
        }
        if (method === 'aicf.getStatus') return { enabled: false, ok: false }
        if (method === 'aicf.getCredits') return { address: TEST_ADDR, balance: '0', events: [] }
        if (method === 'aicf.listJobs') return { items: [] }
        if (method === 'aicf.listPlans') return []
        if (method === 'da.list') {
          throw new Error('DA is not enabled on this node. Run da.configure with enabled=true to activate.')
        }
        return null
      }
    }

    const api = createServer(
      baseService,
      '*',
      'silent',
      {
        mode: 'RPC',
        rpcUrl: 'http://127.0.0.1:8545/rpc',
        chainDbPath: null,
        chainId: 1,
        detectedHead: 0,
        timestamp: new Date().toISOString()
      },
      rpcMock as unknown as import('../src/rpcClient').RpcClient
    )

    const aicfAddress = await request(api).get(`/api/aicf/address/${TEST_ADDR}`)
    expect(aicfAddress.status).toBe(200)
    expect(aicfAddress.body.error).toBeUndefined()

    const daRecent = await request(api).get('/api/da/recent')
    expect(daRecent.status).toBe(200)
    expect(daRecent.body.available).toBe(false)
    expect(Array.isArray(daRecent.body.blobs)).toBe(true)
  })
})
