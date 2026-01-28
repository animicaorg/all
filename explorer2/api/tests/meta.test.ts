import request from 'supertest'
import { describe, expect, it, beforeAll } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'

describe('Meta API', () => {
  let api: ReturnType<typeof createServer>

  beforeAll(async () => {
    const service = new ExplorerService(
      {
        getHead: async () => ({ chainId: 1, height: 100, hash: '0xhead', time: 1000 }),
        getBlockByNumber: async () => ({ header: { height: 100, hash: '0xblock', time: 1000 }, txs: [] }),
        getBlockByHash: async () => ({ header: { height: 100, hash: '0xblock', time: 1000 }, txs: [] }),
        getTransactionByHash: async () => null,
        getTransactionReceipt: async () => null,
        getMempoolPending: async () => [],
        getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
        getPeers: async () => [],
        getBalance: async () => '0x0'
      },
      { head: 1000, blocks: 1000, tx: 1000 }
    )
    
    const diagnostics = {
      mode: 'RPC' as const,
      rpcUrl: 'http://test:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 100,
      timestamp: new Date().toISOString()
    }
    
    api = createServer(service, '*', 'silent', diagnostics)
  })

  it('returns explorer metadata', async () => {
    const res = await request(api).get('/api/meta')
    expect(res.status).toBe(200)
    expect(res.body).toMatchObject({
      explorer: {
        name: 'Animica Explorer',
        version: '0.1.0',
        mode: 'RPC'
      },
      network: {
        chainId: 1,
        rpcUrl: 'http://test:8545/rpc'
      }
    })
    expect(res.body.timestamp).toBeDefined()
  })

  it('returns debug RPC info in non-production', async () => {
    const res = await request(api).get('/api/debug/rpc')
    expect(res.status).toBe(200)
    expect(res.body).toMatchObject({
      mode: 'RPC',
      rpcUrl: 'http://test:8545/rpc',
      timeout: 30000,
      maxRetries: 3
    })
    expect(res.body.timestamp).toBeDefined()
  })

  it('blocks debug endpoint in production', async () => {
    const oldEnv = process.env.NODE_ENV
    process.env.NODE_ENV = 'production'
    
    const res = await request(api).get('/api/debug/rpc')
    expect(res.status).toBe(404)
    expect(res.body.error).toBe('not_found')
    
    process.env.NODE_ENV = oldEnv
  })
})
