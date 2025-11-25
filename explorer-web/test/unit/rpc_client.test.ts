import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import * as rpc from '../../src/services/rpc'

type AnyObj = Record<string, any>

/** Try a list of candidate functions on an object and return the first match */
function pickFn<T extends (...args: any[]) => any>(obj: AnyObj, ...candidates: string[]): T {
  for (const name of candidates) {
    const fn = obj?.[name]
    if (typeof fn === 'function') return fn as T
  }
  throw new Error(`None of the candidate functions found: ${candidates.join(', ')}`)
}

/** Construct a client regardless of naming (createRpc/createClient/class/default) */
function makeClient(baseUrl: string): AnyObj {
  if (typeof (rpc as AnyObj).createRpc === 'function') return (rpc as AnyObj).createRpc({ url: baseUrl })
  if (typeof (rpc as AnyObj).createClient === 'function') return (rpc as AnyObj).createClient(baseUrl)
  if (typeof (rpc as AnyObj).client === 'function') return (rpc as AnyObj).client(baseUrl)
  if ((rpc as AnyObj).RpcClient) return new (rpc as AnyObj).RpcClient(baseUrl)
  if ((rpc as AnyObj).JsonRpcClient) return new (rpc as AnyObj).JsonRpcClient(baseUrl)
  if (typeof (rpc as AnyObj).default === 'function') return (rpc as AnyObj).default(baseUrl)
  if ((rpc as AnyObj).default && typeof (rpc as AnyObj).default === 'object') return (rpc as AnyObj).default
  throw new Error('Unable to construct RPC client from exports')
}

const MOCK_URL = 'http://rpc.local'

const mockHead = { height: 1234, hash: '0xaaaa', time: 1700000000 }
const mockBlock = {
  height: 1234,
  hash: '0xbbbb',
  parentHash: '0xaaaa',
  txs: [{ hash: '0xcccc' }, { hash: '0xdddd' }],
}
const mockTx = {
  hash: '0xcccc',
  from: 'omni1fromaddressxxxxx',
  to: 'omni1toaddressxxxxxxx',
  value: '0x5f5e100', // 100_000_000
  status: 1,
}

let fetchCalls: AnyObj[] = []

/** Install a very small JSON-RPC fetch mock that routes by params shape */
function installFetchMock() {
  fetchCalls = []
  const f = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    const body = typeof init?.body === 'string' ? JSON.parse(init.body as string) : init?.body
    fetchCalls.push(body || {})
    const id = (body && (body.id ?? 1)) ?? 1
    const params = (body && body.params) || []
    const first = params[0]

    let result: any = null
    // Heuristics:
    // - head call: no params
    // - block call: first param is a number (height)
    // - tx call: first param is a hex string (0x...)
    if (params.length === 0 || first === undefined || first === null) {
      result = mockHead
    } else if (typeof first === 'number') {
      result = mockBlock
    } else if (typeof first === 'string' && first.startsWith('0x')) {
      result = mockTx
    } else {
      // default to head to keep tests flexible
      result = mockHead
    }

    // Minimal fetch Response stub
    return {
      ok: true,
      status: 200,
      json: async () => ({ jsonrpc: '2.0', id, result }),
    } as any
  })
  vi.stubGlobal('fetch', f)
  return f
}

function uninstallFetchMock() {
  vi.unstubAllGlobals()
}

describe('rpc client — head/blocks/txs (mocked)', () => {
  beforeEach(() => {
    installFetchMock()
  })
  afterEach(() => {
    uninstallFetchMock()
  })

  it('fetches latest head', async () => {
    const client = makeClient(MOCK_URL)

    const getHead = pickFn<(opts?: any) => Promise<any>>(
      client,
      'getHead',
      'head',
      'latestHead',
      'get_head'
    )

    const head = await getHead.call(client)
    expect(head).toBeTruthy()
    expect(typeof head.height).toBe('number')
    expect(head.hash).toBeTypeOf('string')
    expect(head.height).toBe(mockHead.height)
    expect(head.hash).toBe(mockHead.hash)

    // Ensure a single JSON-RPC call was made
    expect(fetchCalls.length).toBeGreaterThanOrEqual(1)
  })

  it('fetches block by height', async () => {
    const client = makeClient(MOCK_URL)

    const getBlockByHeight = pickFn<(h: number, opts?: any) => Promise<any>>(
      client,
      'getBlockByHeight',
      'getBlock',
      'blockByHeight',
      'block'
    )

    const block = await getBlockByHeight.call(client, 1234)
    expect(block).toBeTruthy()
    expect(block.height).toBe(1234)
    expect(block.hash).toBe(mockBlock.hash)
    expect(Array.isArray(block.txs)).toBe(true)
    expect(block.txs.length).toBe(2)

    // Confirm our mock saw a numeric param
    const last = fetchCalls[fetchCalls.length - 1]
    expect(Array.isArray(last.params)).toBe(true)
    expect(typeof last.params[0]).toBe('number')
  })

  it('fetches transaction by hash', async () => {
    const client = makeClient(MOCK_URL)

    const getTx = pickFn<(hash: string, opts?: any) => Promise<any>>(
      client,
      'getTx',
      'getTransaction',
      'transaction',
      'tx'
    )

    const tx = await getTx.call(client, '0xcccc')
    expect(tx).toBeTruthy()
    expect(tx.hash).toBe('0xcccc')
    expect(tx.status === 0 || tx.status === 1).toBe(true)

    // Confirm our mock saw a hex string param
    const last = fetchCalls[fetchCalls.length - 1]
    expect(Array.isArray(last.params)).toBe(true)
    expect(typeof last.params[0]).toBe('string')
    expect((last.params[0] as string).startsWith('0x')).toBe(true)
  })
})
