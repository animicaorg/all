import request from 'supertest'
import { describe, expect, it } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'

describe('Diagnostics endpoint', () => {
  it('returns diagnostics in RPC mode', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 100, hash: '0xabc123', time: 1700000000 }),
      getBlockByNumber: async () => ({
        header: { height: 100, hash: '0xabc123', parentHash: '0xdef456', time: 1700000000 },
        txs: []
      }),
      getBlockByHash: async () => ({
        header: { height: 100, hash: '0xabc123', parentHash: '0xdef456', time: 1700000000 },
        txs: []
      }),
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    }

    const service = new ExplorerService(mockChainClient, { head: 5000, blocks: 8000, tx: 20000 })

    const diagnostics = {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 100,
      timestamp: new Date().toISOString()
    }

    const api = createServer(service, '*', 'silent', diagnostics)

    const res = await request(api).get('/api/diagnostics')
    expect(res.status).toBe(200)
    expect(res.body.mode).toBe('RPC')
    expect(res.body.rpcUrl).toBe('http://127.0.0.1:8545/rpc')
    expect(res.body.chainId).toBe(1)
    expect(res.body.detectedHead).toBe(100)
    expect(res.body.currentHead).toBeDefined()
    expect(res.body.currentHead.height).toBe(100)
    expect(res.body.database.exists).toBe(false)
  })

  it('returns diagnostics in Local DB mode', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 50, hash: '0xabc', time: 1600000000 }),
      getBlockByNumber: async () => ({
        header: { height: 50, hash: '0xabc', parentHash: '0xdef', time: 1600000000 },
        txs: []
      }),
      getBlockByHash: async () => ({
        header: { height: 50, hash: '0xabc', parentHash: '0xdef', time: 1600000000 },
        txs: []
      }),
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    }

    const service = new ExplorerService(mockChainClient, { head: 5000, blocks: 8000, tx: 20000 })

    const diagnostics = {
      mode: 'Local DB',
      rpcUrl: null,
      chainDbPath: '/home/user/.animica/chain-1/animica.db',
      chainId: 1,
      detectedHead: 50,
      timestamp: new Date().toISOString()
    }

    const api = createServer(service, '*', 'silent', diagnostics)

    const res = await request(api).get('/api/diagnostics')
    expect(res.status).toBe(200)
    expect(res.body.mode).toBe('Local DB')
    expect(res.body.rpcUrl).toBeNull()
    expect(res.body.chainDbPath).toBe('/home/user/.animica/chain-1/animica.db')
    expect(res.body.chainId).toBe(1)
    expect(res.body.detectedHead).toBe(50)
    expect(res.body.currentHead).toBeDefined()
    expect(res.body.currentHead.height).toBe(50)
  })
})

describe('RPC mode behavior', () => {
  it('fetches blocks correctly when RPC returns data', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 10, hash: '0xhead', time: 1700000000 }),
      getBlockByNumber: async (height: number) => ({
        header: { height, hash: `0xblock${height}`, parentHash: `0xparent${height}`, time: 1700000000 + height },
        txs: []
      }),
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    }

    const service = new ExplorerService(mockChainClient, { head: 5000, blocks: 8000, tx: 20000 })
    const diagnostics = {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 10,
      timestamp: new Date().toISOString()
    }

    const api = createServer(service, '*', 'silent', diagnostics)

    // Test /api/blocks
    const blocksRes = await request(api).get('/api/blocks?limit=5')
    expect(blocksRes.status).toBe(200)
    expect(blocksRes.body.items).toBeDefined()
    expect(blocksRes.body.items.length).toBeGreaterThan(0)

    // Test /api/block/:height
    const blockRes = await request(api).get('/api/block/1')
    expect(blockRes.status).toBe(200)
    expect(blockRes.body.height).toBe(1)
  })

  it('returns empty list when head is 0', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 0, hash: '0xgenesis', time: 1700000000 }),
      getBlockByNumber: async (height: number) => {
        if (height === 0) {
          return {
            header: { height: 0, hash: '0xgenesis', parentHash: '0x0', time: 1700000000 },
            txs: []
          }
        }
        throw new Error('Block not found')
      },
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    }

    const service = new ExplorerService(mockChainClient, { head: 5000, blocks: 8000, tx: 20000 })
    const diagnostics = {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 0,
      timestamp: new Date().toISOString()
    }

    const api = createServer(service, '*', 'silent', diagnostics)

    // Test /api/blocks returns genesis only
    const blocksRes = await request(api).get('/api/blocks?limit=5')
    expect(blocksRes.status).toBe(200)
    expect(blocksRes.body.items).toBeDefined()
    expect(blocksRes.body.items.length).toBe(1)
    expect(blocksRes.body.items[0].height).toBe(0)
  })
})

describe('Local DB fallback behavior', () => {
  it('serves blocks from local DB when RPC unavailable', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 5, hash: '0xlocal', time: 1600000000 }),
      getBlockByNumber: async (height: number) => ({
        header: { height, hash: `0xlocal${height}`, parentHash: `0xparent${height}`, time: 1600000000 + height },
        txs: []
      }),
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    }

    const service = new ExplorerService(mockChainClient, { head: 5000, blocks: 8000, tx: 20000 })
    const diagnostics = {
      mode: 'Local DB',
      rpcUrl: null,
      chainDbPath: '/home/user/.animica/chain-1/animica.db',
      chainId: 1,
      detectedHead: 5,
      timestamp: new Date().toISOString()
    }

    const api = createServer(service, '*', 'silent', diagnostics)

    const blocksRes = await request(api).get('/api/blocks?limit=3')
    expect(blocksRes.status).toBe(200)
    expect(blocksRes.body.items.length).toBeGreaterThan(0)
    
    const blockRes = await request(api).get('/api/block/2')
    expect(blockRes.status).toBe(200)
    expect(blockRes.body.height).toBe(2)
  })
})
