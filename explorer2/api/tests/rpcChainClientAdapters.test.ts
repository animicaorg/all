import { describe, expect, it } from 'vitest'
import { RpcChainClient } from '../src/rpcChainClient'

class MockRpc {
  constructor(private readonly responses: Record<string, unknown>) {}

  async call(method: string, _params?: unknown): Promise<unknown> {
    if (method in this.responses) {
      return this.responses[method]
    }
    const err = new Error(`method not found: ${method}`) as Error & { code?: number }
    err.code = -32601
    throw err
  }
}

function makeClient(overrides: Record<string, unknown>) {
  return new RpcChainClient(
    new MockRpc({
      'mempool.getPending': [],
      'p2p.getPeers': [],
      'receipt.getReceipt': null,
      'state.getBalance': '0x0',
      'state.getAccount': {},
      'state.getCode': null,
      'state.getRichList': { items: [] },
      'state.getTotalSupply': { totalSupply: '0x0' },
      ...overrides
    }) as unknown as import('../src/rpcClient').RpcClient
  )
}

describe('RpcChainClient adapter resilience', () => {
  it('extracts tx hashes from mempool object/item variants', async () => {
    const client = makeClient({
      'mempool.getPending': { items: [{ hash: '0xaaa' }, { txHash: '0xbbb' }, '0xccc'] }
    })

    const pending = await client.getMempoolPending()
    expect(pending).toEqual(['0xaaa', '0xbbb', '0xccc'])
  })

  it('normalizes mempool stats snake_case and string fields', async () => {
    const client = makeClient({
      'mempool.getStats': { txCount: '7', total_bytes: '1024', oldest_age_sec: '15' }
    })

    const stats = await client.getMempoolStats()
    expect(stats.count).toBe(7)
    expect(stats.totalBytes).toBe(1024)
    expect(stats.oldestAgeSec).toBe(15)
  })

  it('normalizes numeric/object balance payloads', async () => {
    const numericClient = makeClient({
      'state.getBalance': 25
    })
    const numericBalance = await numericClient.getBalance('anim1test')
    expect(numericBalance).toBe('0x19')

    const objectClient = makeClient({
      'state.getBalance': { amount: 10n }
    })
    const objectBalance = await objectClient.getBalance('anim1test')
    expect(objectBalance).toBe('0xa')
  })
})
