import { describe, expect, it, vi } from 'vitest'
import { ExplorerService } from '../src/service'

const TX_HASH = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'

describe('transaction lookup lifecycle', () => {
  it('pending -> confirmed transition returns included data and confirmations', async () => {
    let confirmed = false

    const service = new ExplorerService({
      getHead: async () => ({ height: 150, hash: '0x' + 'f'.repeat(64), time: 1700000000 }),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => confirmed ? ({ hash: TX_HASH, from: 'anim1a', to: 'anim1b', value: '0x1', blockNumber: 148, blockHash: '0x' + 'b'.repeat(64) }) : null,
      getTransactionReceipt: async () => confirmed ? ({ txHash: TX_HASH, blockHash: '0x' + 'b'.repeat(64), blockNumber: 148, status: 'SUCCESS' }) : null,
      getMempoolPending: async () => confirmed ? [] : [TX_HASH],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const pending = await service.getTxDetail(TX_HASH)
    expect(pending.status).toBe('pending')

    confirmed = true
    const confirmedTx = await service.getTxDetail(TX_HASH)
    expect(confirmedTx.status).toBe('confirmed')
    expect(confirmedTx.included_height).toBe(148)
    expect(confirmedTx.included_block_hash).toBe('0x' + 'b'.repeat(64))
    expect(confirmedTx.confirmations).toBe(3)
  })

  it('lookup normalizes uppercase and no-0x hash formats', async () => {
    const uppercaseNoPrefix = 'A'.repeat(64)
    const service = new ExplorerService({
      getHead: async () => ({ height: 10, hash: '0x' + 'f'.repeat(64), time: 1 }),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async (hash: string) => ({ hash, from: 'anim1a', to: 'anim1b', value: '0x1', blockNumber: 10, blockHash: '0x' + 'c'.repeat(64) }),
      getTransactionReceipt: async (hash: string) => ({ txHash: hash, blockHash: '0x' + 'c'.repeat(64), blockNumber: 10, status: 'SUCCESS' }),
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const tx = await service.getTxDetail(uppercaseNoPrefix)
    expect(tx.tx_hash).toBe('0x' + 'a'.repeat(64))
  })

  it('confirmed lookup survives mempool pruning', async () => {
    const service = new ExplorerService({
      getHead: async () => ({ height: 200, hash: '0x' + 'f'.repeat(64), time: 2 }),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => ({ hash: TX_HASH, from: 'anim1a', to: 'anim1b', value: '0x1', blockNumber: 199, blockHash: '0x' + 'd'.repeat(64) }),
      getTransactionReceipt: async () => ({ txHash: TX_HASH, blockHash: '0x' + 'd'.repeat(64), blockNumber: 199, status: 'SUCCESS' }),
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const tx = await service.getTxDetail(TX_HASH)
    expect(tx.status).toBe('confirmed')
    expect(tx.included_height).toBe(199)
  })
})
