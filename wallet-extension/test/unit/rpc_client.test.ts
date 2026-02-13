import { afterEach, describe, expect, it, vi } from 'vitest'

import { RpcClient } from '../../src/background/network/rpc'

describe('RpcClient param normalization', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('converts tx.sendRawTransaction object params into positional array', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch' as any).mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ jsonrpc: '2.0', id: 1, result: '0xhash' }),
    } as Response)

    const client = new RpcClient({ url: 'https://mainnet.animica.org/rpc' })
    await client.call('tx.sendRawTransaction', { rawTx: '0xdeadbeef' } as any)

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body ?? '{}'))
    expect(body).toMatchObject({
      jsonrpc: '2.0',
      method: 'tx.sendRawTransaction',
      params: ['0xdeadbeef'],
    })
  })

  it('defaults omitted params to an empty array', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch' as any).mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ jsonrpc: '2.0', id: 2, result: 'pong' }),
    } as Response)

    const client = new RpcClient({ url: 'https://mainnet.animica.org/rpc' })
    await client.call('chain.getHead')

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body ?? '{}'))
    expect(body.params).toEqual([])
  })
})
