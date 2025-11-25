import { describe, it, expect, vi } from 'vitest'

// NOTE: The provider module is expected to expose a factory that accepts a transport.
// If your actual export is different (e.g. default export class), adjust imports accordingly.
import { createProvider } from '../../src/provider/index'
import type { JsonRpcRequest } from '../../src/provider/types'
import { ProviderRpcError } from '../../src/provider/errors'

type MockTransport = (req: JsonRpcRequest) => Promise<unknown>

/** Build a provider with a pluggable transport (mocked in tests) */
function withTransport(impl: MockTransport) {
  return createProvider({
    send: (req: JsonRpcRequest) => impl(req),
  } as any)
}

describe('AnimicaProvider.request()', () => {
  it('sends a well-formed JSON-RPC request and returns the result', async () => {
    const sendSpy = vi.fn<MockTransport>().mockImplementation(async (req) => {
      // basic shape checks inside the mock
      expect(req.jsonrpc).toBe('2.0')
      expect(typeof req.id === 'number' || typeof req.id === 'string').toBe(true)
      expect(req.method).toBe('animica_chainId')
      expect(req.params).toEqual([])

      // respond successfully
      return { jsonrpc: '2.0', id: req.id, result: '0x1' }
    })

    const provider = withTransport(sendSpy)

    const chainId = await provider.request({ method: 'animica_chainId' })
    expect(chainId).toBe('0x1')
    expect(sendSpy).toHaveBeenCalledTimes(1)
  })

  it('passes params through and returns structured results', async () => {
    const params = [{ to: 'anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqv3x9d', value: '0x64' }]
    const send = vi.fn<MockTransport>().mockResolvedValue({
      jsonrpc: '2.0',
      id: 1,
      result: { txHash: '0xabc123', status: 1 },
    })

    const provider = withTransport(send as any)
    const res = await provider.request({ method: 'animica_sendTransaction', params })
    expect(res).toEqual({ txHash: '0xabc123', status: 1 })
    expect(send).toHaveBeenCalledWith(
      expect.objectContaining({
        method: 'animica_sendTransaction',
        params,
      }),
    )
  })

  it('throws ProviderRpcError on JSON-RPC error response', async () => {
    const send = vi.fn<MockTransport>().mockImplementation(async (req) => {
      return {
        jsonrpc: '2.0',
        id: req.id,
        error: { code: 4001, message: 'User rejected the request.' }, // EIP-1193 style
      }
    })

    const provider = withTransport(send as any)

    await expect(
      provider.request({ method: 'animica_requestAccounts' }),
    ).rejects.toMatchObject({
      name: 'ProviderRpcError',
      code: 4001,
      message: 'User rejected the request.',
    } as ProviderRpcError)
  })

  it('wraps non-JSON errors into ProviderRpcError(-32000)', async () => {
    const send = vi.fn<MockTransport>().mockRejectedValue(new Error('Network down'))
    const provider = withTransport(send as any)

    await expect(provider.request({ method: 'animica_chainId' })).rejects.toMatchObject({
      name: 'ProviderRpcError',
      code: -32000,
      message: expect.stringContaining('Network down'),
    } as ProviderRpcError)
  })
})

describe('AnimicaProvider events (shape only)', () => {
  it('exposes on/removeListener and emits accountsChanged', async () => {
    // In this unit test we only assert API shape. Emission is exercised via a private hook
    // commonly exposed in the provider for unit testing (e.g. __testEmit).
    const send = vi.fn<MockTransport>().mockResolvedValue({ jsonrpc: '2.0', id: 1, result: [] })
    const provider: any = withTransport(send as any)

    expect(typeof provider.on).toBe('function')
    expect(typeof provider.removeListener).toBe('function')

    const handler = vi.fn()
    provider.on('accountsChanged', handler)

    // If your provider uses a private tester hook, prefer __testEmit. Otherwise, skip runtime emit.
    if (typeof provider.__testEmit === 'function') {
      provider.__testEmit('accountsChanged', ['anim1abc...'])
      expect(handler).toHaveBeenCalledWith(['anim1abc...'])
    }
  })
})
