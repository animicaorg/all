import { describe, expect, test } from 'vitest'
import { sendRawTransaction } from '../src/tx/send'

describe('tx broadcast fallback ladder', () => {
  test('retries -32602 to underscore alias and succeeds', async () => {
    const calls: Array<{ method: string; params: unknown[] }> = []
    const client = {
      call: async (method: string, params?: unknown[]) => {
        calls.push({ method, params: (params ?? []) as unknown[] })
        if (method === 'rpc.discover') return { methods: [] }
        if (method === 'tx.sendRawTransaction') {
          const err: any = new Error('Invalid params')
          err.code = -32602
          throw err
        }
        if (method === 'tx_sendRawTransaction') return '0xabc123'
        throw new Error('unexpected')
      }
    }

    const out = await sendRawTransaction(client as any, '0xdeadbeef')
    expect(out).toBe('0xabc123')
    expect(calls[1]).toEqual({ method: 'tx.sendRawTransaction', params: ['0xdeadbeef'] })
    expect(calls[2]).toEqual({ method: 'tx_sendRawTransaction', params: ['0xdeadbeef'] })
  })

  test('normalizes odd-length tx by padding one nibble', async () => {
    const calls: Array<{ method: string; params: unknown[] }> = []
    const client = {
      call: async (method: string, params?: unknown[]) => {
        calls.push({ method, params: (params ?? []) as unknown[] })
        if (method === 'rpc.discover') return { methods: [{ name: 'tx.sendRawTransaction', params: ['rawTx'] }] }
        if (method === 'tx.sendRawTransaction') return '0xabc123'
        throw new Error('unexpected')
      }
    }

    await sendRawTransaction(client as any, '0xabc')
    expect(calls[1]).toEqual({ method: 'tx.sendRawTransaction', params: ['0x0abc'] })
  })
})
