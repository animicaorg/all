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
    expect(confirmedTx.from).toBe('anim1a')
    expect(confirmedTx.to).toBe('anim1b')
    expect(confirmedTx.value).toBe('0x1')
    expect(confirmedTx.included_height).toBe(148)
    expect(confirmedTx.included_block_hash).toBe('0x' + 'b'.repeat(64))
    expect(confirmedTx.confirmations).toBe(3)
  })

  it('confirmed ingestion from block stores tx body fields for later lookup', async () => {
    const blockHash = '0x' + 'e'.repeat(64)
    const service = new ExplorerService({
      getHead: async () => ({ height: 220, hash: '0x' + 'f'.repeat(64), time: 1700000220 }),
      getBlockByNumber: async (height: number | string) => ({
        header: {
          height: Number(height),
          hash: blockHash,
          time: 1700000220
        },
        txs: [{ hash: TX_HASH, from: 'anim1from', to: 'anim1to', value: '0x99', fee: '0x3' }]
      }),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    await service.getBlockDetail('220')
    const tx = await service.getTxDetail(TX_HASH)

    expect(tx.status).toBe('confirmed')
    expect(tx.from).toBe('anim1from')
    expect(tx.to).toBe('anim1to')
    expect(tx.value).toBe('0x99')
    expect(tx.fee).toBe('0x3')
    expect(tx.included_height).toBe(220)
    expect(tx.included_block_hash).toBe(blockHash)
  })

  it('lazy backfill enriches confirmed row with missing from/to/value', async () => {
    let txCalls = 0
    const service = new ExplorerService({
      getHead: async () => ({ height: 300, hash: '0x' + 'f'.repeat(64), time: 1700000300 }),
      getBlockByNumber: async () => ({
        header: { height: 299, hash: '0x' + 'd'.repeat(64), time: 1700000299 },
        txs: [{ hash: TX_HASH }]
      }),
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => {
        txCalls += 1
        return { hash: TX_HASH, from: 'anim1lazyfrom', to: 'anim1lazyto', value: '0x44', blockNumber: 299, blockHash: '0x' + 'd'.repeat(64) }
      },
      getTransactionReceipt: async () => ({ txHash: TX_HASH, blockHash: '0x' + 'd'.repeat(64), blockNumber: 299, status: 'SUCCESS', feePaid: '0x5' }),
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    await service.getBlockDetail('299')
    const tx = await service.getTxDetail(TX_HASH)

    expect(txCalls).toBe(1)
    expect(tx.from).toBe('anim1lazyfrom')
    expect(tx.to).toBe('anim1lazyto')
    expect(tx.value).toBe('0x44')
    expect(tx.fee).toBe('0x5')
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

  it('recent block scan marks tx as confirmed even when receipts are missing', async () => {
    const blockHash = '0x' + 'c'.repeat(64)
    const service = new ExplorerService({
      getHead: async () => ({ height: 500, hash: '0x' + 'f'.repeat(64), time: 1700000500 }),
      getBlockByNumber: async (height: number | string) => {
        const n = Number(height)
        if (n === 500) {
          return {
            header: { height: 500, hash: blockHash, time: 1700000500 },
            txs: [{ hash: TX_HASH, from: 'anim1scanfrom', to: null, value: '0x7' }],
            receipts: []
          }
        }
        return {
          header: { height: n, hash: '0x' + 'e'.repeat(64), time: 1700000000 + n },
          txs: [],
          receipts: []
        }
      },
      getBlockByHash: vi.fn(),
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0'
    })

    const tx = await service.getTxDetail(TX_HASH)
    expect(tx.status).toBe('confirmed')
    expect(tx.included_height).toBe(500)
    expect(tx.included_block_hash).toBe(blockHash)
    expect(tx.confirmations).toBe(1)
  })
})
