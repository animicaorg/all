import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { RpcClient } from '../src/rpcClient'

describe('RpcClient params payload shape', () => {
  const originalFetch = global.fetch

  beforeEach(() => {
    global.fetch = vi.fn(async (_url, init) => {
      return {
        ok: true,
        json: async () => ({ jsonrpc: '2.0', id: 1, result: init ? JSON.parse(String(init.body)) : {} }),
      } as Response
    }) as typeof fetch
  })

  afterEach(() => {
    global.fetch = originalFetch
    vi.restoreAllMocks()
  })

  it('omits params when undefined', async () => {
    const client = new RpcClient({ url: 'http://127.0.0.1:8545/rpc', maxRetries: 0 })
    const payload = await client.call<Record<string, unknown>>('aicf.status')
    expect(payload.params).toBeUndefined()
  })

  it('passes array params as-is', async () => {
    const client = new RpcClient({ url: 'http://127.0.0.1:8545/rpc', maxRetries: 0 })
    const payload = await client.call<Record<string, unknown>>('x.y', [])
    expect(payload.params).toEqual([])
  })

  it('passes object params without wrapping', async () => {
    const client = new RpcClient({ url: 'http://127.0.0.1:8545/rpc', maxRetries: 0 })
    const payload = await client.call<Record<string, unknown>>('x.y', { foo: 'bar' })
    expect(payload.params).toEqual({ foo: 'bar' })
  })
})
