import { describe, expect, it } from 'vitest'
import { normalizeRpcUrl, classifyHttpError, MAINNET_RPC, LOCAL_RPC } from '../src/urlNormalizer'

describe('normalizeRpcUrl', () => {
  it('returns local default for empty input', () => {
    const r = normalizeRpcUrl('')
    expect(r.url).toBe(LOCAL_RPC)
    expect(r.wasNormalized).toBe(true)
  })

  it('returns local default for null/undefined', () => {
    expect(normalizeRpcUrl(null).url).toBe(LOCAL_RPC)
    expect(normalizeRpcUrl(undefined).url).toBe(LOCAL_RPC)
  })

  it('appends /rpc to bare host', () => {
    const r = normalizeRpcUrl('https://mainnet.animica.org')
    expect(r.url).toBe('https://mainnet.animica.org/rpc')
    expect(r.wasNormalized).toBe(true)
    expect(r.note).toContain('/rpc')
  })

  it('appends /rpc to http://127.0.0.1:8545', () => {
    const r = normalizeRpcUrl('http://127.0.0.1:8545')
    expect(r.url).toBe('http://127.0.0.1:8545/rpc')
    expect(r.wasNormalized).toBe(true)
  })

  it('does not modify already-correct URL', () => {
    const r = normalizeRpcUrl('http://127.0.0.1:8545/rpc')
    expect(r.url).toBe('http://127.0.0.1:8545/rpc')
    expect(r.wasNormalized).toBe(false)
  })

  it('passes through websocket URLs unchanged', () => {
    const r = normalizeRpcUrl('ws://127.0.0.1:8546/ws')
    expect(r.url).toBe('ws://127.0.0.1:8546/ws')
    expect(r.wasNormalized).toBe(false)
  })

  it('passes through wss URLs unchanged', () => {
    const r = normalizeRpcUrl('wss://mainnet.animica.org/ws')
    expect(r.url).toBe('wss://mainnet.animica.org/ws')
    expect(r.wasNormalized).toBe(false)
  })

  it('returns local default for invalid URL', () => {
    const r = normalizeRpcUrl('not a valid url at all :::')
    expect(r.url).toBe(LOCAL_RPC)
    expect(r.wasNormalized).toBe(true)
  })

  it('handles host-only without protocol', () => {
    const r = normalizeRpcUrl('localhost:8545')
    expect(r.url).toContain('/rpc')
    expect(r.wasNormalized).toBe(true)
  })

  it('exports correct mainnet RPC constant', () => {
    expect(MAINNET_RPC).toBe('https://mainnet.animica.org/rpc')
  })
})

describe('classifyHttpError', () => {
  it('classifies 405 as wrong_path_405', () => {
    const c = classifyHttpError(405, 'Method Not Allowed')
    expect(c.kind).toBe('wrong_path_405')
    expect(c.hint).toContain('405')
    expect(c.remediation).toContain('/rpc')
  })

  it('classifies 404 as not_found_404', () => {
    const c = classifyHttpError(404, 'Not Found')
    expect(c.kind).toBe('not_found_404')
    expect(c.remediation).toContain('/rpc')
  })

  it('classifies 401 as auth_401_403', () => {
    const c = classifyHttpError(401, 'Unauthorized')
    expect(c.kind).toBe('auth_401_403')
  })

  it('classifies 403 as auth_401_403', () => {
    const c = classifyHttpError(403, 'Forbidden')
    expect(c.kind).toBe('auth_401_403')
  })

  it('classifies 500 as server_error_5xx', () => {
    const c = classifyHttpError(500, 'Internal Server Error')
    expect(c.kind).toBe('server_error_5xx')
  })

  it('classifies 503 as server_error_5xx', () => {
    const c = classifyHttpError(503, 'Service Unavailable')
    expect(c.kind).toBe('server_error_5xx')
  })

  it('classifies timeout error message', () => {
    const c = classifyHttpError(null, 'Request timeout after 30000ms')
    expect(c.kind).toBe('timeout')
  })

  it('classifies aborted as timeout', () => {
    const c = classifyHttpError(null, 'aborted')
    expect(c.kind).toBe('timeout')
  })

  it('classifies ECONNREFUSED as network', () => {
    const c = classifyHttpError(null, 'ECONNREFUSED 127.0.0.1:8545')
    expect(c.kind).toBe('network')
  })

  it('classifies fetch error as network', () => {
    const c = classifyHttpError(null, 'fetch failed')
    expect(c.kind).toBe('network')
  })

  it('returns unknown for unrecognised errors', () => {
    const c = classifyHttpError(null, 'some weird error')
    expect(c.kind).toBe('unknown')
  })
})
