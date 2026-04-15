import request from 'supertest'
import { describe, expect, it, beforeAll } from 'vitest'
import { createServer } from '../src/server'
import { ExplorerService } from '../src/service'

describe('Explorer API e2e', () => {
  let api: ReturnType<typeof createServer>

  beforeAll(async () => {
    const service = new ExplorerService(
      {
        getHead: async () => ({ chainId: 1, height: 10, hash: '0xabc', time: 1000 }),
        getBlockByNumber: async () => ({
          header: { height: 10, hash: '0xabc', parentHash: '0xdef', time: 1000, theta_micro: '2000000' },
          txs: [{ hash: '0x' + '1'.repeat(64), from: 'anim1from', to: 'anim1to', value: '0x1' }]
        }),
        getBlockByHash: async () => ({
          header: { height: 10, hash: '0xabc', parentHash: '0xdef', time: 1000 },
          txs: [{ hash: '0x' + '1'.repeat(64), from: 'anim1from', to: 'anim1to', value: '0x1' }]
        }),
        getTransactionByHash: async () => ({ hash: '0x' + '1'.repeat(64), from: 'anim1from', to: 'anim1to', value: '0x1' }),
        getTransactionReceipt: async () => ({
          txHash: '0x' + '1'.repeat(64),
          blockHash: '0xabc',
          blockNumber: 10,
          status: 'SUCCESS',
          gasUsed: '0x10',
          logs: []
        }),
        getMempoolPending: async () => ['0x' + '2'.repeat(64)],
        getMempoolStats: async () => ({ count: 1, totalBytes: 120, oldestAgeSec: 3 }),
        getPeers: async () => [{ direction: 'inbound' }, { direction: 'outbound' }],
        getBalance: async () => '0x5'
      },
      { head: 1000, blocks: 1000, tx: 1000 }
    )
    api = createServer(service, '*', 'silent')
  })

  it('serves head stats', async () => {
    const res = await request(api).get('/api/head')
    expect(res.status).toBe(200)
    expect(res.body.head.height).toBe(10)
    expect(res.body.stats.peerCount).toBe(2)
    expect(Array.isArray(res.body.thetaHistory)).toBe(true)
    expect(res.body.thetaHistory[0].thetaMicro).toBe(2_000_000)
  })

  it('serves block detail', async () => {
    const res = await request(api).get('/api/block/10')
    expect(res.status).toBe(200)
    expect(res.body.hash).toBe('0xabc')
  })

  it('serves tx detail', async () => {
    const res = await request(api).get('/api/tx/0x1111111111111111111111111111111111111111111111111111111111111111')
    expect(res.status).toBe(200)
    expect(res.body.status).toBe('confirmed')
  })
})
