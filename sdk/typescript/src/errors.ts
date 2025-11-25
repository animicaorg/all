/**
 * Typed error helpers for the Animica TypeScript SDK.
 * - JSON-RPC errors (RpcError)
 * - Transaction submission/execution errors (TxError)
 * - ABI/schema validation errors (AbiError)
 * - Verification/consistency errors (VerifyError)
 */

export const JSON_RPC_ERROR_CODES = {
  PARSE_ERROR: -32700,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  INTERNAL_ERROR: -32603,
  // -32000..-32099 reserved for server errors (Geth-style and custom)
} as const

/** Minimal JSON-RPC 2.0 types used for error mapping. */
export interface JsonRpcError {
  code: number
  message: string
  data?: unknown
}

export type JsonRpcId = string | number | null

export interface JsonRpcSuccess<T = unknown> {
  jsonrpc: '2.0'
  id: JsonRpcId
  result: T
}

export interface JsonRpcFailure {
  jsonrpc: '2.0'
  id: JsonRpcId
  error: JsonRpcError
}

export type JsonRpcResponse<T = unknown> = JsonRpcSuccess<T> | JsonRpcFailure

/** Narrow error-like shapes without forcing instanceof checks across realms. */
export function isErrorLike(x: unknown): x is { message: string } {
  return !!x && typeof x === 'object' && 'message' in x && typeof (x as any).message === 'string'
}

/** Coerce unknown into an Error with best-effort message. */
export function ensureError(e: unknown, fallback = 'Unknown error'): Error {
  if (e instanceof Error) return e
  if (isErrorLike(e)) return new Error((e as any).message)
  try {
    return new Error(typeof e === 'string' ? e : JSON.stringify(e))
  } catch {
    return new Error(fallback)
  }
}

/** Base SDK error with optional machine-readable fields. */
export class BaseError extends Error {
  /** Optional numeric code (e.g., JSON-RPC error code or HTTP status). */
  readonly code?: number
  /** Arbitrary structured data (e.g., JSON-RPC error.data). */
  readonly data?: unknown
  /** Original cause (native ErrorOptions.cause compatible). */
  override readonly cause?: unknown
  /** Additional context fields (safe to log). */
  readonly context?: Record<string, unknown>

  constructor(
    message: string,
    opts: {
      code?: number
      data?: unknown
      cause?: unknown
      context?: Record<string, unknown>
    } = {}
  ) {
    super(message, 'cause' in Error.prototype ? { cause: opts.cause } : undefined as any)
    this.name = new.target.name
    this.code = opts.code
    this.data = opts.data
    this.cause = opts.cause
    this.context = opts.context
  }
}

/** JSON-RPC error with method & request id. */
export class RpcError extends BaseError {
  readonly method: string
  readonly requestId: JsonRpcId | undefined

  constructor(
    method: string,
    message: string,
    opts: {
      id?: JsonRpcId
      code?: number
      data?: unknown
      cause?: unknown
      context?: Record<string, unknown>
    } = {}
  ) {
    super(message, opts)
    this.method = method
    this.requestId = opts.id
  }
}

/** Transaction-related errors (admission, validation, or execution). */
export class TxError extends BaseError {
  readonly txHash?: string
  readonly receipt?: unknown // shape depends on RPC; keep generic
  readonly status?: number | string

  constructor(
    message: string,
    opts: {
      code?: number
      data?: unknown
      cause?: unknown
      txHash?: string
      receipt?: unknown
      status?: number | string
      context?: Record<string, unknown>
    } = {}
  ) {
    super(message, opts)
    this.txHash = opts.txHash
    this.receipt = opts.receipt
    this.status = opts.status
  }
}

/** ABI/schema validation errors (bad types, missing fields, etc.). */
export class AbiError extends BaseError {
  readonly path?: string
  readonly schemaErrors?: readonly unknown[]

  constructor(
    message: string,
    opts: {
      code?: number
      data?: unknown
      cause?: unknown
      path?: string
      schemaErrors?: readonly unknown[]
      context?: Record<string, unknown>
    } = {}
  ) {
    super(message, opts)
    this.path = opts.path
    this.schemaErrors = opts.schemaErrors
  }
}

/** Verification/consistency failures for light clients or artifact checks. */
export class VerifyError extends BaseError {
  readonly field?: string
  readonly expectation?: string
  readonly actual?: string

  constructor(
    message: string,
    opts: {
      code?: number
      data?: unknown
      cause?: unknown
      field?: string
      expectation?: string
      actual?: string
      context?: Record<string, unknown>
    } = {}
  ) {
    super(message, opts)
    this.field = opts.field
    this.expectation = opts.expectation
    this.actual = opts.actual
  }
}

/** Type guard for RpcError. */
export function isRpcError(e: unknown): e is RpcError {
  return !!e && e instanceof RpcError
}

/** Type guard for TxError. */
export function isTxError(e: unknown): e is TxError {
  return !!e && e instanceof TxError
}

/**
 * Map a JSON-RPC failure response into an RpcError.
 */
export function rpcErrorFromResponse(
  method: string,
  response: JsonRpcFailure,
  extraContext?: Record<string, unknown>
): RpcError {
  const { id, error } = response
  const ctx = { id, response, ...extraContext }
  return new RpcError(method, error?.message || 'RPC error', {
    id,
    code: error?.code,
    data: error?.data,
    context: ctx
  })
}

/**
 * Normalize arbitrary thrown values from an RPC call into RpcError.
 */
export function asRpcError(
  method: string,
  thrown: unknown,
  opts?: { id?: JsonRpcId; context?: Record<string, unknown> }
): RpcError {
  // If it's already RpcError, re-wrap with merged context
  if (thrown instanceof RpcError) {
    const merged = { ...(thrown.context || {}), ...(opts?.context || {}) }
    return new RpcError(thrown.method || method, thrown.message, {
      id: opts?.id ?? thrown.requestId,
      code: thrown.code,
      data: thrown.data,
      cause: thrown.cause,
      context: merged
    })
  }

  // If it looks like a JSON-RPC failure payload, map it
  const t = thrown as any
  if (t && typeof t === 'object' && t.error && typeof t.error.message === 'string') {
    return rpcErrorFromResponse(method, t as JsonRpcFailure, opts?.context)
  }

  const err = ensureError(thrown)
  return new RpcError(method, err.message, {
    id: opts?.id,
    cause: err,
    context: opts?.context
  })
}

/**
 * Build a TxError from a receipt-like object or structured failure.
 */
export function txErrorFromReceipt(
  message: string,
  receipt?: any,
  extras?: {
    txHash?: string
    code?: number
    data?: unknown
    status?: number | string
    context?: Record<string, unknown>
  }
): TxError {
  const ctx = { receipt, ...extras?.context }
  return new TxError(message, {
    txHash: extras?.txHash ?? receipt?.transactionHash ?? receipt?.txHash,
    code: extras?.code,
    data: extras?.data ?? receipt,
    status: extras?.status ?? receipt?.status,
    context: ctx
  })
}

/**
 * Human-friendly stringification of an error for logs.
 */
export function formatError(e: unknown): string {
  if (e instanceof BaseError) {
    const parts = [e.name, e.message]
    if ((e as any).method) parts.push(`method=${(e as any).method}`)
    if ((e as any).requestId !== undefined) parts.push(`id=${String((e as any).requestId)}`)
    if (e.code !== undefined) parts.push(`code=${e.code}`)
    return parts.join(' | ')
  }
  const err = ensureError(e)
  return `${err.name}: ${err.message}`
}

/**
 * Utility: unwrap JsonRpcResponse, or throw RpcError if it is a failure.
 */
export function unwrapJsonRpc<T = unknown>(
  method: string,
  res: JsonRpcResponse<T>
): T {
  if ('error' in res) {
    throw rpcErrorFromResponse(method, res)
  }
  return (res as JsonRpcSuccess<T>).result
}
