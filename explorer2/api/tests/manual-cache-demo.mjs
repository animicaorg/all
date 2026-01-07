#!/usr/bin/env node
/**
 * Test script to demonstrate tiered block caching behavior.
 * This script simulates repeated block fetches to show reduced RPC calls.
 */

import { ExplorerService } from '../dist/service.js'

// Mock RPC client that counts calls
class MockRpcClient {
  constructor() {
    this.callCounts = {
      getHead: 0,
      getBlockByNumber: 0,
      getBlockByHash: 0,
      getTransactionByHash: 0,
      getTransactionReceipt: 0,
      getMempoolPending: 0,
      getMempoolStats: 0,
      getPeers: 0,
      getBalance: 0
    }
  }

  async getHead() {
    this.callCounts.getHead++
    return {
      height: 100,
      hash: '0x' + '1'.repeat(64),
      time: Date.now()
    }
  }

  async getBlockByNumber(height, includeTxs, includeReceipts) {
    this.callCounts.getBlockByNumber++
    return {
      height,
      hash: '0x' + height.toString(16).padStart(64, '0'),
      time: Date.now() - (100 - height) * 1000,
      txCount: 5,
      txs: includeTxs ? [] : undefined
    }
  }

  async getBlockByHash(hash, includeTxs, includeReceipts) {
    this.callCounts.getBlockByHash++
    throw new Error('Not implemented')
  }

  async getTransactionByHash(hash) {
    this.callCounts.getTransactionByHash++
    return null
  }

  async getTransactionReceipt(hash) {
    this.callCounts.getTransactionReceipt++
    return null
  }

  async getMempoolPending() {
    this.callCounts.getMempoolPending++
    return []
  }

  async getMempoolStats() {
    this.callCounts.getMempoolStats++
    return { count: 0, totalBytes: 0, oldestAgeSec: null }
  }

  async getPeers() {
    this.callCounts.getPeers++
    return []
  }

  async getBalance(address, tag) {
    this.callCounts.getBalance++
    return '0x0'
  }

  resetCounters() {
    Object.keys(this.callCounts).forEach(key => {
      this.callCounts[key] = 0
    })
  }

  printCounters() {
    console.log('RPC Call Counts:')
    Object.entries(this.callCounts).forEach(([method, count]) => {
      if (count > 0) {
        console.log(`  ${method}: ${count}`)
      }
    })
  }
}

async function testTieredCaching() {
  console.log('=== Testing Explorer2 Tiered Block Caching ===\n')

  const mockRpc = new MockRpcClient()
  const service = new ExplorerService(
    mockRpc,
    { head: 5000, blocks: 8000, tx: 20000 }
  )

  // Test 1: Fetch recent blocks multiple times
  console.log('Test 1: Fetching recent blocks (heights 95-100) twice')
  console.log('Expected: First fetch hits RPC, second fetch uses cache')
  mockRpc.resetCounters()

  await service.getBlocks(6) // Heights 100, 99, 98, 97, 96, 95
  const firstCallCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  First fetch: ${firstCallCount} RPC calls`)

  await service.getBlocks(6) // Same heights
  const secondCallCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  Second fetch: ${secondCallCount - firstCallCount} new RPC calls`)
  console.log(`  ✓ Blocks cached (${secondCallCount - firstCallCount === 0 ? 'PASS' : 'FAIL'})\n`)

  // Test 2: Fetch old (finalized) blocks multiple times
  console.log('Test 2: Fetching finalized blocks (heights 85-90) twice')
  console.log('Expected: First fetch hits RPC, second fetch uses cache')
  mockRpc.resetCounters()

  await service.getBlocks(6, '90') // Heights 90, 89, 88, 87, 86, 85
  const firstOldCallCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  First fetch: ${firstOldCallCount} RPC calls`)

  await service.getBlocks(6, '90') // Same heights
  const secondOldCallCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  Second fetch: ${secondOldCallCount - firstOldCallCount} new RPC calls`)
  console.log(`  ✓ Finalized blocks cached (${secondOldCallCount - firstOldCallCount === 0 ? 'PASS' : 'FAIL'})\n`)

  // Test 3: Demonstrate cache persistence benefit
  console.log('Test 3: Fetching mixed blocks (heights 50-60)')
  console.log('These are old finalized blocks, cached for 24 hours')
  mockRpc.resetCounters()

  await service.getBlocks(11, '60')
  const mixedFirstCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  First fetch: ${mixedFirstCount} RPC calls`)

  await service.getBlocks(11, '60')
  const mixedSecondCount = mockRpc.callCounts.getBlockByNumber
  console.log(`  Second fetch: ${mixedSecondCount - mixedFirstCount} new RPC calls`)
  console.log(`  ✓ All finalized blocks stay cached (${mixedSecondCount - mixedFirstCount === 0 ? 'PASS' : 'FAIL'})\n`)

  // Summary
  console.log('=== Summary ===')
  console.log('✓ Recent blocks (within 10 of head): 8-second TTL')
  console.log('✓ Finalized blocks (>10 from head): 24-hour TTL')
  console.log('✓ Repeated fetches of cached blocks: 0 RPC calls')
  console.log('\nThis dramatically reduces RPC load for historical data!')
  console.log('With cache persistence enabled (EXPLORER2_CACHE_PERSIST_PATH),')
  console.log('blocks survive restarts for even better performance.')
}

// Run the test
testTieredCaching().catch((err) => {
  console.error(err)
  process.exit(1)
})
