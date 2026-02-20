import { describe, it, expect } from 'vitest'
import { normalizeRpcUrl, formatError, bigIntSafeStringify, parseHexQuantity } from './rpcUtils'

describe('normalizeRpcUrl (frontend)', () => {
  it('appends /rpc to bare host', () => {
    const r = normalizeRpcUrl('https://mainnet.animica.org')
    expect(r.url).toBe('https://mainnet.animica.org/rpc')
    expect(r.wasNormalized).toBe(true)
  })

  it('does not modify correct URL', () => {
    const r = normalizeRpcUrl('https://mainnet.animica.org/rpc')
    expect(r.url).toBe('https://mainnet.animica.org/rpc')
    expect(r.wasNormalized).toBe(false)
  })

  it('passes through ws:// unchanged', () => {
    const r = normalizeRpcUrl('ws://localhost:8546/ws')
    expect(r.url).toBe('ws://localhost:8546/ws')
    expect(r.wasNormalized).toBe(false)
  })

  it('returns local default for empty string', () => {
    const r = normalizeRpcUrl('')
    expect(r.url).toContain('127.0.0.1')
    expect(r.wasNormalized).toBe(true)
  })
})

describe('formatError (frontend)', () => {
  it('classifies 405 message correctly', () => {
    const fe = formatError(new Error('HTTP 405 Method Not Allowed'))
    expect(fe.kind).toBe('wrong_path_405')
    expect(fe.remediation).toContain('/rpc')
  })

  it('classifies method not found', () => {
    const fe = formatError(new Error('method not found: aicf.getStatus'))
    expect(fe.kind).toBe('rpc_method_not_found')
  })

  it('classifies network error', () => {
    const fe = formatError(new Error('fetch failed: ECONNREFUSED'))
    expect(fe.kind).toBe('network')
  })

  it('never returns [object Object] for object errors', () => {
    const fe = formatError({ code: -32601, message: 'method not found' })
    expect(fe.message).not.toBe('[object Object]')
    expect(typeof fe.message).toBe('string')
  })

  it('handles null/undefined gracefully', () => {
    const fe = formatError(null)
    expect(typeof fe.message).toBe('string')
    expect(typeof fe.hint).toBe('string')
    expect(typeof fe.remediation).toBe('string')
  })
})

describe('bigIntSafeStringify (frontend)', () => {
  it('serialises BigInt without throwing', () => {
    expect(() => bigIntSafeStringify({ val: 12345678901234567890n })).not.toThrow()
    expect(bigIntSafeStringify({ val: 42n })).toBe('{"val":"42"}')
  })

  it('handles circular references', () => {
    const o: Record<string, unknown> = {}
    o.self = o
    expect(() => bigIntSafeStringify(o)).not.toThrow()
  })
})

describe('parseHexQuantity (frontend)', () => {
  it('parses standard 0x values', () => {
    expect(parseHexQuantity('0x3b9aca00')).toBe(1000000000n)
  })

  it('returns 0n for empty/null', () => {
    expect(parseHexQuantity(null)).toBe(0n)
    expect(parseHexQuantity('')).toBe(0n)
  })
})
