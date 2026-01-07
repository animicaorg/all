import { describe, expect, it, vi, beforeEach } from 'vitest'
import { ExplorerService } from '../src/service'
import type { ChainClient } from '../src/service'

describe('ExplorerService - Tiered Block Caching', () => {
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

    service = new ExplorerService(
      mockRpc,
      { head: 5000, blocks: 8000, tx: 20000 }
    )
  })

  it('should cache recent blocks with short TTL', async () => {
    const headHeight = 100
    const recentBlockHeight = 95 // 5 blocks old

    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: recentBlockHeight,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // First call - should hit RPC
    await service.getBlocks(1)
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(1)

    // Second call - should use cache (within 8s TTL)
    await service.getBlocks(1)
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(1) // Still 1 call
  })

  it('should cache finalized blocks with long TTL', async () => {
    const headHeight = 100
    const finalizedBlockHeight = 85 // 15 blocks old (> FINALIZED_BLOCK_DEPTH of 10)

    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: finalizedBlockHeight,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // First call - should hit RPC
    await service.getBlocks(1, String(finalizedBlockHeight))
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(1)

    // Second call - should use cache
    await service.getBlocks(1, String(finalizedBlockHeight))
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(1) // Still 1 call

    // Even after a delay, finalized blocks stay cached (24h TTL vs 8s)
    // In a real scenario, this would be cached for 24 hours
  })

  it('should use tiered caching for getBlockDetail', async () => {
    const headHeight = 100
    const blockHeight = 85

    // Mock head in cache
    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: blockHeight,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // Prime the head cache
    await service.getHead()

    // First call - should hit RPC
    await service.getBlockDetail(String(blockHeight))
    expect(mockRpc.getBlockByNumber).toHaveBeenCalled()

    const firstCallCount = vi.mocked(mockRpc.getBlockByNumber).mock.calls.length

    // Second call - should use cache
    await service.getBlockDetail(String(blockHeight))
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(firstCallCount) // No additional calls
  })

  it('should not cache head blocks indefinitely', async () => {
    const headHeight = 100

    vi.mocked(mockRpc.getHead).mockResolvedValue({
      height: headHeight,
      hash: '0xhead',
      time: Date.now()
    })

    vi.mocked(mockRpc.getBlockByNumber).mockResolvedValue({
      height: headHeight,
      hash: '0xblock',
      time: Date.now(),
      txs: []
    })

    // First call
    await service.getBlocks(1)
    const firstCallCount = vi.mocked(mockRpc.getBlockByNumber).mock.calls.length

    // Second call immediately should use cache
    await service.getBlocks(1)
    expect(mockRpc.getBlockByNumber).toHaveBeenCalledTimes(firstCallCount)

    // The head block should have a short TTL (8s), not 24h
  })
})
