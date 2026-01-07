import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ExplorerService } from '../src/service'
import type { ChainClient } from '../src/service'

describe('ExplorerService - Request Coalescing', () => {
  let mockRpc: ChainClient
  let service: ExplorerService

  beforeEach(() => {
    mockRpc = {
      getHead: vi.fn(),
      getBlockByNumber: vi.fn(),
      getBlockByHash: vi.fn(),
      getTransactionByHash: vi.fn(),
      getTransactionReceipt: vi.fn(),
      getMempoolPending: vi.fn(),
      getMempoolStats: vi.fn(),
      getPeers: vi.fn(),
      getBalance: vi.fn()
    }

    service = new ExplorerService(mockRpc)
  })

  it('should make RPC calls for blocks', async () => {
    const headHeight = 100

    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: 95,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    vi.mocked(mockRpc.getMempoolStats).mockResolvedValue({
      count: 0,
      totalBytes: 0,
      oldestAgeSec: null
    })

    vi.mocked(mockRpc.getPeers).mockResolvedValue([])

    // First call - should hit RPC
    await service.getBlocks(1)
    expect(mockRpc.getBlockByNumber).toHaveBeenCalled()
  })

  it('should make RPC calls for block details', async () => {
    const blockHeight = 85

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: blockHeight,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // Call should hit RPC
    await service.getBlockDetail(String(blockHeight))
    expect(mockRpc.getBlockByNumber).toHaveBeenCalled()
  })

  it('should coalesce concurrent requests to the same resource', async () => {
    const headHeight = 100

    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getMempoolStats).mockResolvedValue({
      count: 0,
      totalBytes: 0,
      oldestAgeSec: null
    })

    vi.mocked(mockRpc.getPeers).mockResolvedValue([])

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: 95,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // Make two concurrent calls - should only call RPC once due to coalescing
    const [result1, result2] = await Promise.all([
      service.getHead(),
      service.getHead()
    ])

    expect(result1.head.height).toBe(headHeight)
    expect(result2.head.height).toBe(headHeight)
    expect(mockRpc.getHead).toHaveBeenCalledTimes(1)
  })
})
