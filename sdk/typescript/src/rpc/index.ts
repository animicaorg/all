/**
 * @module rpc
 * Public entry for JSON-RPC transports used by @animica/sdk.
 *
 * - Exposes typed JSON-RPC request/response shapes
 * - Defines a minimal transport interface
 * - Re-exports concrete HTTP and WebSocket clients
 *
 * Usage:
 *   import { createHttpClient } from '@animica/sdk/rpc'
 *   const rpc = createHttpClient('http://localhost:8545')
 *   const head = await rpc.request<Head>('chain.getHead')
 */

export type JsonRpcId = string | number | null

export interface JsonRpcRequest<P = unknown> {
  jsonrpc: '2.0'
  method: string
  params?: P
  id: JsonRpcId
}

export interface JsonRpcErrorObject<D = unknown> {
  code: number
  message: string
  data?: D
}

export interface JsonRpcSuccess<R = unknown> {
  jsonrpc: '2.0'
  result: R
  id: JsonRpcId
}

export interface JsonRpcFailure<D = unknown> {
  jsonrpc: '2.0'
  error: JsonRpcErrorObject<D>
  id: JsonRpcId
}

export type JsonRpcResponse<R = unknown, D = unknown> = JsonRpcSuccess<R> | JsonRpcFailure<D>

/** Options common to transports */
export interface RequestOptions {
  /** Abort the in-flight request */
  signal?: AbortSignal
  /** Per-request timeout (ms). Implemented by transports that support it. */
  timeoutMs?: number
}

/**
 * Minimal transport interface used throughout the SDK. Concrete implementations
 * (HTTP and WS) extend this with extra helpers (batch, subscriptions).
 */
export interface RpcTransport {
  request<R = unknown, P = unknown>(method: string, params?: P, opts?: RequestOptions): Promise<R>
}

/** Guard for JSON-RPC failure responses */
export function isJsonRpcFailure(x: unknown): x is JsonRpcFailure {
  return !!x && typeof x === 'object' && 'error' in (x as any) && (x as any).jsonrpc === '2.0'
}

/** Guard for JSON-RPC success responses */
export function isJsonRpcSuccess<R = unknown>(x: unknown): x is JsonRpcSuccess<R> {
  return !!x && typeof x === 'object' && 'result' in (x as any) && (x as any).jsonrpc === '2.0'
}

// Re-export common domain types so callers can import from '.../rpc'
export { Head, BlockView, TxView, SignedTx, Receipt, Hash, Address } from '../types/core'

// Re-export SDK error types
export * from '../errors'

// Re-export concrete transports & factories (implemented in sibling files)
export * from './http'
export * from './ws'
