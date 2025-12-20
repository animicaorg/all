export type JsonRpcId = number | string | null

export interface JsonRpcRequest<P = unknown> {
  jsonrpc: '2.0'
  method: string
  params?: P
  id: JsonRpcId
}

export interface JsonRpcSuccess<R = unknown> {
  jsonrpc: '2.0'
  result: R
  id: JsonRpcId
}

export interface JsonRpcFailure {
  jsonrpc: '2.0'
  error: { code: number; message: string; data?: unknown }
  id: JsonRpcId
}

export type JsonRpcResponse<R = unknown> = JsonRpcSuccess<R> | JsonRpcFailure

export class JsonRpcClient {
  private id = 1

  constructor(private url: string) {}

  async request<R = unknown, P = unknown>(method: string, params?: P): Promise<R> {
    const payload: JsonRpcRequest<P> = {
      jsonrpc: '2.0',
      method,
      params,
      id: this.id++
    }

    const res = await fetch(this.url, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        accept: 'application/json'
      },
      body: JSON.stringify(payload)
    })

    if (!res.ok) {
      throw new Error(`RPC HTTP ${res.status}`)
    }

    const data = (await res.json()) as JsonRpcResponse<R>
    if ('error' in data) {
      throw new Error(data.error.message)
    }
    return data.result
  }
}
