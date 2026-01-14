import { describe, expect, it } from 'vitest'
import { ExplorerService } from '../src/service'

describe('getHead returns recentBlocks', () => {
  it('includes recentBlocks array with miner information', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 10, hash: '0xhead', time: 1700000000 }),
      getBlockByNumber: async (height: number) => ({
        header: { 
          height, 
          hash: `0xblock${height}`, 
          parentHash: `0xparent${height}`, 
          time: 1700000000 + height,
          miner: `anim1miner${height}`
        },
        txs: []
      }),
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0',
      getRichList: async () => ({ entries: [], totalSupply: '0x0', totalAccounts: 0, hasMore: false })
    }

    const service = new ExplorerService(mockChainClient)
    const result = await service.getHead()

    // Verify structure
    expect(result.head).toBeDefined()
    expect(result.stats).toBeDefined()
    expect(result.recentBlocks).toBeDefined()
    expect(Array.isArray(result.recentBlocks)).toBe(true)
    
    // Verify recentBlocks contains data
    expect(result.recentBlocks.length).toBeGreaterThan(0)
    
    // Verify first block has miner information
    const firstBlock = result.recentBlocks[0]
    expect(firstBlock.height).toBeDefined()
    expect(firstBlock.hash).toBeDefined()
    expect(firstBlock.miner).toBeDefined()
    expect(firstBlock.miner).toMatch(/^anim1miner/)
  })

  it('handles blocks without miner information gracefully', async () => {
    const mockChainClient = {
      getHead: async () => ({ chainId: 1, height: 5, hash: '0xhead', time: 1700000000 }),
      getBlockByNumber: async (height: number) => ({
        header: { 
          height, 
          hash: `0xblock${height}`, 
          parentHash: `0xparent${height}`, 
          time: 1700000000 + height
          // Note: no miner field
        },
        txs: []
      }),
      getBlockByHash: async () => null,
      getTransactionByHash: async () => null,
      getTransactionReceipt: async () => null,
      getMempoolPending: async () => [],
      getMempoolStats: async () => ({ count: 0, totalBytes: 0, oldestAgeSec: null }),
      getPeers: async () => [],
      getBalance: async () => '0x0',
      getRichList: async () => ({ entries: [], totalSupply: '0x0', totalAccounts: 0, hasMore: false })
    }

    const service = new ExplorerService(mockChainClient)
    const result = await service.getHead()

    expect(result.recentBlocks).toBeDefined()
    expect(result.recentBlocks.length).toBeGreaterThan(0)
    
    // Verify blocks without miner have undefined or null miner
    const firstBlock = result.recentBlocks[0]
    expect(firstBlock.miner).toBeUndefined()
  })
})
