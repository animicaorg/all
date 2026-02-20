/**
 * URL normalizer for Animica RPC endpoints.
 * Handles: bare hosts → /rpc appended, ws:// detection, 405 diagnosis.
 */

export const MAINNET_RPC = 'https://mainnet.animica.org/rpc'
export const LOCAL_RPC = 'http://127.0.0.1:8545/rpc'

/**
 * Normalize an RPC URL:
 * - If falsy, return the local default.
 * - If it's a ws/wss URL, return as-is (caller handles WebSocket).
 * - If the pathname is "/" or empty, append "/rpc".
 * - Strip trailing slashes on the path.
 */
export function normalizeRpcUrl(raw: string | null | undefined): { url: string; wasNormalized: boolean; note?: string } {
  if (!raw || raw.trim() === '') {
    return { url: LOCAL_RPC, wasNormalized: true, note: 'Defaulted to local node at ' + LOCAL_RPC }
  }

  const input = raw.trim()

  // WebSocket URLs: pass through unchanged
  if (input.startsWith('ws://') || input.startsWith('wss://')) {
    return { url: input, wasNormalized: false }
  }

  let parsed: URL
  try {
    // If no protocol, add http:// to allow URL parsing
    const withProtocol = input.includes('://') ? input : `http://${input}`
    parsed = new URL(withProtocol)
  } catch {
    return { url: LOCAL_RPC, wasNormalized: true, note: `Invalid URL "${input}", defaulted to local node` }
  }

  const wasNormalized = (parsed.pathname === '/' || parsed.pathname === '')
  if (wasNormalized) {
    parsed.pathname = '/rpc'
  }

  return {
    url: parsed.toString(),
    wasNormalized,
    note: wasNormalized ? `Auto-appended /rpc to "${input}"` : undefined
  }
}

/**
 * Classify HTTP errors into actionable kinds.
 */
export type HttpErrorKind =
  | 'wrong_path_405'   // 405 = likely hit wrong path (e.g. /  instead of /rpc)
  | 'not_found_404'    // 404 = wrong path or not running
  | 'auth_401_403'     // Authentication required
  | 'server_error_5xx' // Server-side error
  | 'timeout'          // Request timeout
  | 'network'          // DNS / TLS / connection refused
  | 'cors'             // CORS blocked
  | 'unknown'

export interface ClassifiedError {
  kind: HttpErrorKind
  hint: string
  remediation: string
  originalMessage: string
}

export function classifyHttpError(status: number | null, message: string): ClassifiedError {
  const base = { originalMessage: message }

  if (status === 405) {
    return {
      ...base,
      kind: 'wrong_path_405',
      hint: 'HTTP 405 Method Not Allowed — the RPC endpoint likely requires a /rpc path suffix.',
      remediation: 'Change your URL from e.g. https://mainnet.animica.org to https://mainnet.animica.org/rpc'
    }
  }
  if (status === 404) {
    return {
      ...base,
      kind: 'not_found_404',
      hint: 'HTTP 404 Not Found — node may be offline or the path is wrong.',
      remediation: 'Verify the node is running and the URL path is correct (should end in /rpc)'
    }
  }
  if (status === 401 || status === 403) {
    return {
      ...base,
      kind: 'auth_401_403',
      hint: `HTTP ${status} — authentication required or access denied.`,
      remediation: 'Check that your API key or access token is correctly configured'
    }
  }
  if (status !== null && status >= 500) {
    return {
      ...base,
      kind: 'server_error_5xx',
      hint: `HTTP ${status} — server-side error on the node.`,
      remediation: 'Check the node logs; the service may be starting up or have crashed'
    }
  }

  const msg = message.toLowerCase()
  if (msg.includes('timeout') || msg.includes('aborted')) {
    return {
      ...base,
      kind: 'timeout',
      hint: 'Request timed out — the node is slow or unreachable.',
      remediation: 'Increase EXPLORER2_RPC_TIMEOUT_MS or check node performance'
    }
  }
  if (msg.includes('cors') || msg.includes('blocked')) {
    return {
      ...base,
      kind: 'cors',
      hint: 'CORS error — the node does not allow cross-origin requests from this origin.',
      remediation: 'Enable CORS on the node or run the explorer on the same origin'
    }
  }
  if (msg.includes('fetch') || msg.includes('network') || msg.includes('econnrefused') || msg.includes('dns')) {
    return {
      ...base,
      kind: 'network',
      hint: 'Network error — cannot reach the node.',
      remediation: 'Check that the node is running and the URL/port are correct'
    }
  }

  return {
    ...base,
    kind: 'unknown',
    hint: 'Unknown error communicating with the node.',
    remediation: 'Check the Explorer diagnostics page for more details'
  }
}
