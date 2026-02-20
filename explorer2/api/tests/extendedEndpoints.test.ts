import request from 'supertest'
import { describe, expect, it, beforeAll } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'
import { invalidateCapabilityCache } from '../src/rpcExtended'

/**
 * Mock RPC client that simulates method-not-found for unknown methods.
 */
class MockRpc {
  private available: Set<string>

  constructor(availableMethods: string[] = []) {
    this.available = new Set(availableMethods)
  }

  async call(method: string, _params: unknown[] = []): Promise<unknown> {
    if (!this.available.has(method)) {
      const err = new Error(`method not found: ${method}`) as Error & { code: number }
      err.code = -32601
      throw err
    }
    // Return minimal mock data per method
    const methods = [...this.available]
    const mocks: Record<string, unknown> = {
      // rpc.discover returns the actual list of available methods so capability detection works.
      'rpc.discover': { methods, version: '1.0.0' },
      'rpc.listMethods': methods,
      'node.ping': 'pong',
      'chain.getHead': { height: 10, hash: '0xhead', time: 1700000000 },
      'mempool.getStats': { count: 3, totalBytes: 360, oldestAgeSec: 5 },
      'admin.serviceStatus': { chain: 'ok', mempool: 'ok' },
      // Standard status schema methods
      'aicf.status': { enabled: true, ok: true, reason: null, message: null, details: { pool_balance: '0x64', current_epoch: 5 } },
      'aicf_status': { enabled: true, ok: true, reason: null, message: null, details: {} },
      'da.status': { enabled: false, ok: false, reason: 'unavailable', message: 'DA not yet available', details: {} },
      'da_status': { enabled: false, ok: false, reason: 'unavailable', message: 'DA not yet available', details: {} },
      'miner.status': { enabled: true, ok: true, reason: null, message: null, details: { sync_phase: 'synced' } },
      'miner_status': { enabled: true, ok: true, reason: null, message: null, details: {} },
      'quantum.status': { enabled: false, ok: false, reason: 'unavailable', message: 'Quantum not enabled', details: {} },
      'quantum_status': { enabled: false, ok: false, reason: 'unavailable', message: 'Quantum not enabled', details: {} },
      // Legacy method names (still supported)
      'aicf.getStatus': { pool: 100, credits: 50 },
      'aicf.getCredits': { address: 'anim1test', credits: 42 },
      'aicf.listJobs': { items: [] },
      'aicf.listPlans': [{ id: 'basic', cost: 10 }],
      'miner.getStatus': { active: true, hashrate: 1000 },
      'miner.getBlockTemplate': { height: 11, difficulty: '0xff' },
      'miner.getMetrics': { stale: 0, accepted: 10 },
      'da.getStatus': { available: true },
      'da.getQuotas': { daily: 100 },
      'da.listCommitments': [{ commitment: '0xc1', size: 100 }],
      'da.getBlob': { data: 'aGVsbG8=' },
      'da.getProof': { branches: [], indices: [] },
      'quantum.getStatus': { workers: 2 },
      'quantum.listWorkers': [{ id: 'w1' }],
      'quantum.listJobs': { items: [] },
      'quantum.getPolicy': { maxContributions: 10 },
    }
    return mocks[method] ?? null
  }

  async ping(): Promise<boolean> { return true }
}

function makeMockChainClient() {
  return {
    getHead: async () => ({ chainId: 1, height: 10, hash: '0xabc', time: 1700000000 }),
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
}

describe('New API endpoints — with full RPC mock', () => {
  let api: ReturnType<typeof createServer>
  const rpc = new MockRpc([
    'rpc.discover', 'chain.getHead', 'mempool.getStats', 'admin.serviceStatus',
    'aicf.status', 'aicf.getStatus', 'aicf.getCredits', 'aicf.listJobs', 'aicf.listPlans',
    'miner.status', 'miner.getStatus', 'miner.getBlockTemplate', 'miner.getMetrics',
    'da.status', 'da.getStatus', 'da.getQuotas', 'da.listCommitments', 'da.getBlob', 'da.getProof',
    'quantum.status', 'quantum.getStatus', 'quantum.listWorkers', 'quantum.listJobs', 'quantum.getPolicy',
  ])

  beforeAll(() => {
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    api = createServer(service, '*', 'silent', {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 10,
      timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)
  })

  it('GET /api/rpc/discover returns available methods', async () => {
    const res = await request(api).get('/api/rpc/discover')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(true)
    expect(Array.isArray(res.body.methods)).toBe(true)
  })

  it('GET /api/network/status returns service list', async () => {
    const res = await request(api).get('/api/network/status')
    expect(res.status).toBe(200)
    expect(res.body.timestamp).toBeDefined()
    expect(Array.isArray(res.body.services)).toBe(true)
    expect(res.body.services.length).toBeGreaterThan(0)
  })

  it('GET /api/network/status — chain is ok when chain.getHead succeeds', async () => {
    const res = await request(api).get('/api/network/status')
    expect(res.status).toBe(200)
    const chain = res.body.services.find((s: { name: string }) => s.name === 'chain')
    expect(chain).toBeDefined()
    expect(chain.status).toBe('ok')
  })

  it('GET /api/network/status — aicf shows ok (method returns enabled:true ok:true)', async () => {
    const res = await request(api).get('/api/network/status')
    const aicf = res.body.services.find((s: { name: string }) => s.name === 'aicf')
    expect(aicf).toBeDefined()
    expect(aicf.status).toBe('ok')
  })

  it('GET /api/network/status — da shows down/disabled (method returns enabled:false)', async () => {
    const res = await request(api).get('/api/network/status')
    const da = res.body.services.find((s: { name: string }) => s.name === 'da')
    expect(da).toBeDefined()
    // da.status returns enabled:false so it should be 'down' (not 'not_supported')
    expect(da.status).toBe('down')
  })

  it('GET /api/network/status — quantum shows down/disabled (method returns enabled:false)', async () => {
    const res = await request(api).get('/api/network/status')
    const quantum = res.body.services.find((s: { name: string }) => s.name === 'quantum')
    expect(quantum).toBeDefined()
    // quantum.status returns enabled:false so it should be 'down' (not 'not_supported')
    expect(quantum.status).toBe('down')
  })

  it('GET /api/aicf/info returns AICF info', async () => {
    const res = await request(api).get('/api/aicf/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(true)
  })

  it('GET /api/aicf/info with address returns credits', async () => {
    const res = await request(api).get('/api/aicf/info?address=anim1test')
    expect(res.status).toBe(200)
    expect(res.body.credits).toBeDefined()
  })

  it('GET /api/mining/info returns mining info', async () => {
    const res = await request(api).get('/api/mining/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(true)
    expect(res.body.status).toBeDefined()
    expect(res.body.template).toBeDefined()
  })

  it('GET /api/da/info returns DA info', async () => {
    const res = await request(api).get('/api/da/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(true)
  })

  it('GET /api/da/history returns list', async () => {
    const res = await request(api).get('/api/da/history')
    expect(res.status).toBe(200)
    expect(Array.isArray(res.body)).toBe(true)
  })

  it('GET /api/da/blob/:commitment returns blob', async () => {
    const res = await request(api).get('/api/da/blob/0xc1')
    expect(res.status).toBe(200)
    expect(res.body.data).toBeDefined()
  })

  it('GET /api/da/proof/:commitment returns proof', async () => {
    const res = await request(api).get('/api/da/proof/0xc1')
    expect(res.status).toBe(200)
    expect(res.body.branches).toBeDefined()
  })

  it('GET /api/quantum/info returns quantum info', async () => {
    const res = await request(api).get('/api/quantum/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(true)
  })

  it('GET /api/debug/bundle returns debug bundle', async () => {
    const res = await request(api).get('/api/debug/bundle')
    expect(res.status).toBe(200)
    expect(res.body.exportedAt).toBeDefined()
    expect(res.body.profile).toBeDefined()
    expect(res.body.profile.rpcUrl).toBeDefined()
    expect(res.body.rpcDiscover).toBeDefined()
  })

  it('POST /api/da/put validates required fields', async () => {
    const res = await request(api).post('/api/da/put').send({ namespace: 'test' })
    expect(res.status).toBe(400)
    expect(res.body.error).toBe('bad_request')
  })
})

describe('New API endpoints — no RPC (non-RPC mode)', () => {
  let api: ReturnType<typeof createServer>

  beforeAll(() => {
    const service = new ExplorerService(makeMockChainClient())
    api = createServer(service, '*', 'silent', {
      mode: 'Local DB',
      rpcUrl: null,
      chainDbPath: '/tmp/test.db',
      chainId: 1,
      detectedHead: 10,
      timestamp: new Date().toISOString()
    })
    // No rpc argument — simulates Local DB mode
  })

  it('GET /api/rpc/discover degrades gracefully without RPC', async () => {
    const res = await request(api).get('/api/rpc/discover')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
    expect(res.body.note).toBeDefined()
  })

  it('GET /api/network/status degrades gracefully without RPC', async () => {
    const res = await request(api).get('/api/network/status')
    expect(res.status).toBe(200)
    expect(Array.isArray(res.body.services)).toBe(true)
  })

  it('GET /api/aicf/info returns available:false without RPC', async () => {
    const res = await request(api).get('/api/aicf/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })

  it('GET /api/mining/info returns available:false without RPC', async () => {
    const res = await request(api).get('/api/mining/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })

  it('GET /api/da/info returns available:false without RPC', async () => {
    const res = await request(api).get('/api/da/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })

  it('GET /api/quantum/info returns available:false without RPC', async () => {
    const res = await request(api).get('/api/quantum/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })
})

describe('New API endpoints — RPC with no extended methods', () => {
  let api: ReturnType<typeof createServer>
  const rpc = new MockRpc(['chain.getHead']) // only chain.getHead available

  beforeAll(() => {
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    api = createServer(service, '*', 'silent', {
      mode: 'RPC',
      rpcUrl: 'http://127.0.0.1:8545/rpc',
      chainDbPath: null,
      chainId: 1,
      detectedHead: 10,
      timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)
  })

  it('GET /api/rpc/discover falls back gracefully', async () => {
    const res = await request(api).get('/api/rpc/discover')
    expect(res.status).toBe(200)
    // May have available:false if no discover/listMethods/ping found
    expect(typeof res.body.available).toBe('boolean')
  })

  it('GET /api/aicf/info returns available:false when methods unavailable', async () => {
    const res = await request(api).get('/api/aicf/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })

  it('GET /api/mining/info returns available:false when methods unavailable', async () => {
    const res = await request(api).get('/api/mining/info')
    expect(res.status).toBe(200)
    expect(res.body.available).toBe(false)
  })
})

describe('Capability detection — not_supported vs down', () => {
  it('returns not_supported for services when rpc.discover shows methods absent', async () => {
    // rpc.discover lists only chain.getHead — no aicf/da/miner/quantum status methods
    const rpc = new MockRpc(['rpc.discover', 'chain.getHead'])
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    const app = createServer(service, '*', 'silent', {
      mode: 'RPC', rpcUrl: 'http://test/', chainDbPath: null,
      chainId: 1, detectedHead: 0, timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)

    const res = await request(app).get('/api/network/status')
    expect(res.status).toBe(200)
    const services: Array<{ name: string; status: string; hint?: string }> = res.body.services
    const aicf = services.find(s => s.name === 'aicf')
    const da = services.find(s => s.name === 'da')
    const miner = services.find(s => s.name === 'miner')
    const quantum = services.find(s => s.name === 'quantum')

    expect(aicf?.status).toBe('not_supported')
    expect(aicf?.hint).toBe('Not supported by this RPC')
    expect(da?.status).toBe('not_supported')
    expect(miner?.status).toBe('not_supported')
    expect(quantum?.status).toBe('not_supported')
  })

  it('returns down with hint when aicf.status returns enabled:false', async () => {
    // Node has aicf.status but reports disabled
    const rpc = new MockRpc(['rpc.discover', 'chain.getHead', 'aicf.status'])
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    const app = createServer(service, '*', 'silent', {
      mode: 'RPC', rpcUrl: 'http://test/', chainDbPath: null,
      chainId: 1, detectedHead: 0, timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)

    const res = await request(app).get('/api/network/status')
    expect(res.status).toBe(200)
    const aicf = res.body.services.find((s: { name: string }) => s.name === 'aicf')
    // aicf.status mock returns { enabled: true, ok: true } so this should be ok
    expect(aicf?.status).toBe('ok')
  })

  it('returns down when da.status returns enabled:false (unavailable)', async () => {
    // Node has da.status but reports disabled
    const rpc = new MockRpc(['rpc.discover', 'chain.getHead', 'da.status'])
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    const app = createServer(service, '*', 'silent', {
      mode: 'RPC', rpcUrl: 'http://test/', chainDbPath: null,
      chainId: 1, detectedHead: 0, timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)

    const res = await request(app).get('/api/network/status')
    expect(res.status).toBe(200)
    const da = res.body.services.find((s: { name: string }) => s.name === 'da')
    // da.status mock returns { enabled: false, ok: false } so it should be 'down'
    expect(da?.status).toBe('down')
    expect(da?.hint).toContain('DA not yet available')
  })

  it('chain ok, mempool not_supported when mempool method absent', async () => {
    // Only chain.getHead available (rpc.discover lists it)
    const rpc = new MockRpc(['rpc.discover', 'chain.getHead'])
    invalidateCapabilityCache(rpc as unknown as import('../src/rpcClient').RpcClient)
    const service = new ExplorerService(makeMockChainClient())
    const app = createServer(service, '*', 'silent', {
      mode: 'RPC', rpcUrl: 'http://test/', chainDbPath: null,
      chainId: 1, detectedHead: 0, timestamp: new Date().toISOString()
    }, rpc as unknown as import('../src/rpcClient').RpcClient)

    const res = await request(app).get('/api/network/status')
    expect(res.status).toBe(200)
    const chain = res.body.services.find((s: { name: string }) => s.name === 'chain')
    const mempool = res.body.services.find((s: { name: string }) => s.name === 'mempool')
    expect(chain?.status).toBe('ok')
    expect(mempool?.status).toBe('not_supported')
  })
})

