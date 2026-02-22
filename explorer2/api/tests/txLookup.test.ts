import { describe, expect, it, vi } from 'vitest'
import { ExplorerService } from '../src/service'
import { normalizeTxHash } from '../src/txHash'

describe('transaction lookup lifecycle', () => {
  it('returns confirmed tx by hash after mempool eviction', async () => {
    const txHash = '0xAbC123'
    const service = new ExplorerService({
      getHead: async () => ({ height: 99, hash: '0xhead', time: 1 }),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async (hash: string) => ({ hash, from: 'anim1a', to: 'anim1b', value: '0x1' }),
      getTransactionReceipt: async (hash: string) => ({ txHash: hash, blockHash: '0xblock', blockNumber: 90, status: 'SUCCESS' }),
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const tx = await service.getTxDetail(txHash)
    expect(tx.status).toBe('confirmed')
    expect(tx.blockHeight).toBe(90)
    expect(tx.hash).toBe(normalizeTxHash(txHash))
  })

  it('falls back to mempool lookup when receipt and tx are missing', async () => {
    const txHash = 'ABCDEF'
    const service = new ExplorerService({
      getHead: async () => ({ height: 1, hash: '0x1', time: 1 }),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => ['0xabcdef'],
      getMempoolStats: async () => ({ count: 1, totalBytes: 10, oldestAgeSec: 1 }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const tx = await service.getTxDetail(txHash)
    expect(tx.status).toBe('pending')
    expect(tx.hash).toBe('0xabcdef')
  })
})
