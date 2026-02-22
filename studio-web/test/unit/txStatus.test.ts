import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getTxStatus } from '../../src/services/txStatus'

const mockRpc = {
  getHead: vi.fn(),
  getTransactionByHash: vi.fn(),
  getTransactionReceipt: vi.fn()
}

vi.mock('../../src/services/rpc', () => ({
  getRpc: () => mockRpc
}))

describe('getTxStatus', () => {
  beforeEach(() => {
    vi.resetAllMocks()
  })

  it('transitions pending -> confirmed', async () => {
    mockRpc.getHead.mockResolvedValue({ height: 20 })
    mockRpc.getTransactionByHash.mockResolvedValue({ hash: '0xabc', pending: true })
    mockRpc.getTransactionReceipt.mockResolvedValue(null)
    const pending = await getTxStatus('ABC')
    expect(pending.status).toBe('pending')

    mockRpc.getTransactionByHash.mockResolvedValue({ hash: '0xabc', blockNumber: 18 })
    mockRpc.getTransactionReceipt.mockResolvedValue({ blockNumber: 18, status: 'SUCCESS' })
    const confirmed = await getTxStatus('0xAbC')
    expect(confirmed.status).toBe('confirmed')
    expect(confirmed.confirmations).toBe(3)
  })

  it('marks dropped after timeout', async () => {
    mockRpc.getHead.mockResolvedValue({ height: 20 })
    mockRpc.getTransactionByHash.mockResolvedValue(null)
    mockRpc.getTransactionReceipt.mockResolvedValue(null)
    const out = await getTxStatus('0xdef', { firstSeenAt: Date.now() - 700_000, dropAfterMs: 600_000 })
    expect(out.status).toBe('dropped')
  })

  it('reverts confirmed to pending on reorg-like disappearance', async () => {
    mockRpc.getHead.mockResolvedValue({ height: 20 })
    mockRpc.getTransactionByHash.mockResolvedValue({ hash: '0xabc', blockNumber: 19 })
    mockRpc.getTransactionReceipt.mockResolvedValue({ blockNumber: 19, status: 'SUCCESS' })
    const confirmed = await getTxStatus('0xabc')
    expect(confirmed.status).toBe('confirmed')

    mockRpc.getTransactionByHash.mockResolvedValue({ hash: '0xabc', pending: true })
    mockRpc.getTransactionReceipt.mockResolvedValue(null)
    const pending = await getTxStatus('0xabc', { firstSeenAt: Date.now() })
    expect(pending.status).toBe('pending')
  })
})
