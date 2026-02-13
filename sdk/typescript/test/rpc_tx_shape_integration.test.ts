import { afterAll, beforeAll, describe, expect, test } from 'vitest'
import http from 'node:http'
import { createHttpClient } from '../src/rpc/http'
import { sendRawTransaction } from '../src/tx/send'

let server: http.Server
let port = 0

describe('tx broadcast json-rpc shape integration', () => {
  beforeAll(async () => {
    server = http.createServer((req, res) => {
      let body = ''
      req.on('data', (chunk) => { body += chunk })
      req.on('end', () => {
        const payload = JSON.parse(body)
        if (payload.method === 'rpc.discover') {
          res.writeHead(200, { 'content-type': 'application/json' })
          res.end(JSON.stringify({ jsonrpc: '2.0', id: payload.id, result: { methods: [{ name: 'tx.sendRawTransaction', params: ['rawTx'] }] } }))
          return
        }
        if (payload.method === 'tx.sendRawTransaction') {
          if (Array.isArray(payload.params)) {
            res.writeHead(200, { 'content-type': 'application/json' })
            res.end(JSON.stringify({ jsonrpc: '2.0', id: payload.id, result: '0xfeed' }))
            return
          }
          res.writeHead(200, { 'content-type': 'application/json' })
          res.end(JSON.stringify({ jsonrpc: '2.0', id: payload.id, error: { code: -32602, message: 'Invalid params' } }))
          return
        }
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ jsonrpc: '2.0', id: payload.id, error: { code: -32601, message: 'Method not found' } }))
      })
    })
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
    port = (server.address() as any).port
  })

  afterAll(async () => {
    await new Promise<void>((resolve, reject) => server.close((err) => (err ? reject(err) : resolve())))
  })

  test('uses positional params for tx broadcast', async () => {
    const client = createHttpClient(`http://127.0.0.1:${port}`)
    const txHash = await sendRawTransaction({ call: client.request.bind(client) } as any, '0xdeadbeef')
    expect(txHash).toBe('0xfeed')
  })
})
