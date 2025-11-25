import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import http from 'node:http'

// Lightweight JSON-RPC 2.0 mock server for tests
let server: http.Server
let url: string

beforeAll(async () => {
  server = http.createServer(async (req, res) => {
    if (req.method !== 'POST' || req.url !== '/rpc') {
      res.writeHead(404).end()
      return
    }
    const chunks: Buffer[] = []
    for await (const c of req) chunks.push(c as Buffer)
    const bodyStr = Buffer.concat(chunks).toString('utf8')
    let payload: any
    try {
      payload = JSON.parse(bodyStr)
    } catch {
      res.writeHead(400, { 'content-type': 'application/json' }).end(JSON.stringify({
        jsonrpc: '2.0', error: { code: -32700, message: 'Parse error' }, id: null
      }))
      return
    }

    const handle = (reqObj: any) => {
      const { method, params, id } = reqObj ?? {}
      if (!reqObj || reqObj.jsonrpc !== '2.0' || typeof method !== 'string') {
        return { jsonrpc: '2.0', error: { code: -32600, message: 'Invalid Request' }, id: id ?? null }
      }
      try {
        switch (method) {
          case 'chain.getHead':
            return { jsonrpc: '2.0', result: { height: 42, hash: '0xabc123' }, id }
          case 'echo.sum': {
            const arr = Array.isArray(params) ? params : []
            const sum = arr.reduce((a, b) => a + Number(b || 0), 0)
            return { jsonrpc: '2.0', result: sum, id }
          }
          default:
            return { jsonrpc: '2.0', error: { code: -32601, message: 'Method not found' }, id }
        }
      } catch (e: any) {
        return { jsonrpc: '2.0', error: { code: -32000, message: e?.message || 'Server error' }, id }
      }
    }

    const response = Array.isArray(payload)
      ? payload.map(handle)
      : handle(payload)

    res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify(response))
  })

  await new Promise<void>((resolve) => server.listen(0, resolve))
  const addr = server.address()
  if (!addr || typeof addr === 'string') throw new Error('no addr')
  url = `http://127.0.0.1:${addr.port}/rpc`
})

afterAll(async () => {
  await new Promise<void>((resolve) => server.close(() => resolve()))
})

// Helper to instantiate whatever the SDK exports for HTTP RPC
async function makeClient(endpoint: string): Promise<any> {
  const rpcHttp: any = await import('../src/rpc/http')
  // Prefer factory if present
  if (typeof rpcHttp.createHttpClient === 'function') return rpcHttp.createHttpClient({ url: endpoint })
  const Cls = rpcHttp.HttpClient || rpcHttp.default || rpcHttp.Client || rpcHttp.JsonRpcHttp || rpcHttp.JsonRpcClient
  if (!Cls) throw new Error('Unable to find HTTP RPC client export')
  try {
    return new Cls({ url: endpoint }) // config style
  } catch {
    return new Cls(endpoint) // direct url style
  }
}

// Helper to call a method regardless of client surface
async function rpcCall(client: any, method: string, params?: any): Promise<any> {
  if (typeof client.request === 'function') return client.request(method, params)
  if (typeof client.call === 'function') return client.call(method, params)
  if (typeof client.send === 'function') return client.send(method, params)
  throw new Error('Client has no request/call/send method')
}

// Optional: batch helper if available
async function rpcBatch(client: any, calls: { method: string; params?: any }[]): Promise<any[]> {
  if (typeof client.batch === 'function') return client.batch(calls)
  // Fallback: parallel singles
  return Promise.all(calls.map(c => rpcCall(client, c.method, c.params)))
}

describe('HTTP JSON-RPC client roundtrip', () => {
  test('simple call returns a result', async () => {
    const client = await makeClient(url)
    const head = await rpcCall(client, 'chain.getHead', [])
    expect(head).toBeTruthy()
    expect(head.height).toBe(42)
    expect(head.hash).toBeTypeOf('string')
  })

  test('call with params', async () => {
    const client = await makeClient(url)
    const sum = await rpcCall(client, 'echo.sum', [1, 2, 3, 4])
    expect(sum).toBe(10)
  })

  test('unknown method surfaces structured error', async () => {
    const client = await makeClient(url)
    try {
      await rpcCall(client, 'does.not.exist', [])
      throw new Error('expected to throw')
    } catch (err: any) {
      const msg = String(err?.message ?? err)
      // Accept either forwarded JSON-RPC error or wrapped error text
      expect(msg).toMatch(/not found|RPC|code|-32601/i)
    }
  })

  test('batch calls (if supported) resolve to array of results', async () => {
    const client = await makeClient(url)
    const out = await rpcBatch(client, [
      { method: 'echo.sum', params: [5, 5] },
      { method: 'chain.getHead' }
    ])
    expect(Array.isArray(out)).toBe(true)
    // Fallback path (parallel singles) returns [10, {height:42...}]
    // Native batch may preserve mapping; both acceptable for this smoke test.
    const first = out[0]
    const second = out[1]
    // One of them should be the sum
    expect([first, second]).toContain(10)
    // One should be the head object
    const maybeHead = first && typeof first === 'object' ? first : (second && typeof second === 'object' ? second : null)
    expect(maybeHead && maybeHead.height).toBe(42)
  })
})
