import request from 'supertest'
import { describe, expect, it, beforeAll } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'

describe('Search API', () => {
  let api: ReturnType<typeof createServer>

  beforeAll(async () => {
    const service = new ExplorerService(
      {
        getHead: async () => ({ chainId: 1, height: 100, hash: '0xhead', time: 1000 }),
        getBlockByNumber: async (height: number) => ({
          header: { height, hash: `0xblock${height}`, parentHash: '0xparent', time: 1000 },
          txs: []
        }),
        getBlockByHash: async (hash: string) => ({
          header: { height: 50, hash, parentHash: '0xparent', time: 1000 },
          txs: []
        }),
        getTransactionByHash: async (hash: string) => ({ 
          hash, 
          from: 'anim1from', 
          to: 'anim1to', 
          value: '0x1' 
        }),
        getTransactionReceipt: async (hash: string) => ({
          txHash: hash,
          blockHash: '0xblock',
          blockNumber: 50,
          status: 'SUCCESS',
          gasUsed: '0x10',
          logs: []
        }),
        getMempoolPending: async () => [],
        getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
        getPeers: async () => [],
        getBalance: async (address: string) => '0x100'
      },
      { head: 1000, blocks: 1000, tx: 1000 }
    )
    api = createServer(service, '*', 'silent')
  })

  it('searches for block by height', async () => {
    const res = await request(api).get('/api/search?q=42')
    expect(res.status).toBe(200)
    expect(res.body.type).toBe('block')
    expect(res.body.result.height).toBe(42)
  })

  it('searches for transaction by hash', async () => {
    const res = await request(api).get('/api/search?q=0x1111111111111111111111111111111111111111111111111111111111111111')
    expect(res.status).toBe(200)
    expect(res.body.type).toBe('tx')
    expect(res.body.result.hash).toBe('0x' + '1'.repeat(64))
  })

  it('searches for address', async () => {
    const res = await request(api).get('/api/search?q=anim1testaddress')
    expect(res.status).toBe(200)
    expect(res.body.type).toBe('address')
    expect(res.body.result.address).toBe('anim1testaddress')
  })

  it('returns none for empty query', async () => {
    const res = await request(api).get('/api/search?q=')
    expect(res.status).toBe(200)
    expect(res.body.type).toBe('none')
  })

  it('returns none for invalid query', async () => {
    const res = await request(api).get('/api/search?q=invalid')
    expect(res.status).toBe(200)
    expect(res.body.type).toBe('none')
  })
})
