/**
 * RPC utility functions for the Explorer2 frontend.
 * Handles URL normalization, error formatting, and BigInt-safe JSON.
 */

export const MAINNET_RPC = 'https://mainnet.animica.org/rpc'
export const LOCAL_RPC = 'http://127.0.0.1:8545/rpc'

// ── URL Normalization ─────────────────────────────────────────────────────────

export interface UrlNormResult {
  url: string
  wasNormalized: boolean
  note?: string
}

export function normalizeRpcUrl(raw: string | null | undefined): UrlNormResult {
  if (!raw || raw.trim() === '') {
    return { url: LOCAL_RPC, wasNormalized: true, note: `Defaulted to local node at ${LOCAL_RPC}` }
  }
  const input = raw.trim()
  if (input.startsWith('ws://') || input.startsWith('wss://')) {
    return { url: input, wasNormalized: false }
  }
  try {
    const withProtocol = input.includes('://') ? input : `http://${input}`
    const parsed = new URL(withProtocol)
    const wasNormalized = parsed.pathname === '/' || parsed.pathname === ''
    if (wasNormalized) parsed.pathname = '/rpc'
    return {
      url: parsed.toString(),
      wasNormalized,
      note: wasNormalized ? `Auto-appended /rpc to "${input}"` : undefined,
    }
  } catch {
    return { url: LOCAL_RPC, wasNormalized: true, note: `Invalid URL "${input}", defaulted to local node` }
  }
}

// ── Error Formatting ──────────────────────────────────────────────────────────

export type ErrorKind =
  | 'wrong_path_405'
  | 'not_found_404'
  | 'auth_401_403'
  | 'server_error_5xx'
  | 'timeout'
  | 'network'
  | 'cors'
  | 'rpc_method_not_found'
  | 'rpc_invalid_params'
  | 'rpc_server_error'
  | 'not_available'
  | 'unknown'

export interface FormattedError {
  kind: ErrorKind
  message: string
  hint: string
  remediation: string
  details?: string
}

export function formatError(err: unknown): FormattedError {
  if (err instanceof Error) {
    const msg = err.message
    return classifyMessage(msg)
  }
  if (typeof err === 'string') {
    return classifyMessage(err)
  }
  if (err && typeof err === 'object') {
    const o = err as Record<string, unknown>
    const msg = typeof o.message === 'string' ? o.message : JSON.stringify(err)
    const base = classifyMessage(msg)
    if (typeof o.hint === 'string') base.hint = o.hint
    if (typeof o.remediation === 'string') base.remediation = o.remediation
    return base
  }
  return {
    kind: 'unknown',
    message: 'An unexpected error occurred',
    hint: 'No additional information available',
    remediation: 'Check the Diagnostics page for more details',
  }
}

function classifyMessage(msg: string): FormattedError {
  const lower = msg.toLowerCase()

  if (lower.includes('405') || lower.includes('method not allowed')) {
    return {
      kind: 'wrong_path_405',
      message: msg,
      hint: 'HTTP 405 — the endpoint requires a /rpc path suffix.',
      remediation: 'Update the RPC URL to end with /rpc (e.g. https://mainnet.animica.org/rpc)',
    }
  }
  if (lower.includes('method not found') || lower.includes('-32601')) {
    return {
      kind: 'rpc_method_not_found',
      message: msg,
      hint: 'This RPC method is not available on the connected node.',
      remediation: 'Check the RPC Inspector page to see available methods',
    }
  }
  if (lower.includes('404') || lower.includes('not found')) {
    return {
      kind: 'not_found_404',
      message: msg,
      hint: 'Resource not found — node may be offline or path is wrong.',
      remediation: 'Verify the node is running and the URL path ends with /rpc',
    }
  }
  if (lower.includes('401') || lower.includes('403') || lower.includes('unauthorized') || lower.includes('forbidden')) {
    return {
      kind: 'auth_401_403',
      message: msg,
      hint: 'Authentication required or access denied.',
      remediation: 'Check your API key or access credentials',
    }
  }
  if (lower.match(/5\d\d/) || lower.includes('internal server error') || lower.includes('service unavailable')) {
    return {
      kind: 'server_error_5xx',
      message: msg,
      hint: 'Server-side error — the node may be starting up or crashed.',
      remediation: 'Check the node logs; retry in a moment',
    }
  }
  if (lower.includes('timeout') || lower.includes('aborted') || lower.includes('timed out')) {
    return {
      kind: 'timeout',
      message: msg,
      hint: 'Request timed out — node is slow or unreachable.',
      remediation: 'Check node performance or increase the RPC timeout',
    }
  }
  if (lower.includes('cors') || lower.includes('blocked by')) {
    return {
      kind: 'cors',
      message: msg,
      hint: 'CORS error — the node does not allow cross-origin requests.',
      remediation: 'Enable CORS on the node or use the Explorer API proxy',
    }
  }
  if (lower.includes('fetch') || lower.includes('network') || lower.includes('econnrefused') || lower.includes('dns') || lower.includes('failed to connect')) {
    return {
      kind: 'network',
      message: msg,
      hint: 'Cannot reach the node — check network connectivity.',
      remediation: 'Verify the node is running and the URL/port are correct',
    }
  }
  if (lower.includes('not available') || lower.includes('not supported')) {
    return {
      kind: 'not_available',
      message: msg,
      hint: 'This feature is not available on the connected node.',
      remediation: 'Use rpc.discover to check what methods are available',
    }
  }

  return {
    kind: 'unknown',
    message: msg || 'Unknown error',
    hint: 'An unexpected error occurred.',
    remediation: 'Check the Diagnostics and RPC Inspector pages for more details',
  }
}

// ── BigInt-safe JSON ──────────────────────────────────────────────────────────

export function bigIntSafeStringify(value: unknown, space?: number): string {
  const seen = new WeakSet()
  return JSON.stringify(
    value,
    (_key, val) => {
      if (typeof val === 'bigint') return val.toString()
      if (typeof val === 'object' && val !== null) {
        if (seen.has(val)) return '[Circular]'
        seen.add(val)
      }
      return val
    },
    space,
  )
}

// ── Hex Quantity Parsing ──────────────────────────────────────────────────────

export function parseHexQuantity(value: string | null | undefined): bigint {
  if (!value || value === '0x' || value === '0X') return 0n
  try {
    const s = value.startsWith('0x') || value.startsWith('0X') ? value : `0x${value}`
    return BigInt(s)
  } catch {
    return 0n
  }
}

export function formatHexAsDecimal(value: string | null | undefined): string {
  const n = parseHexQuantity(value)
  return n.toLocaleString('en-US')
}
