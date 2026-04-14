/**
 * RPC-based ChainClient implementation.
 * Connects directly to the Animica node's JSON-RPC endpoint.
 */

import { RpcClient } from './rpcClient.js'
import type { ChainClient } from './service.js'
import pino from 'pino'
import { normalizeTxHash } from './txHash.js'

const log = pino({ name: 'rpc-chain-client' })

/**
 * Capabilities detected from the node.
 */
interface Capabilities {
  hasMempool: boolean
  hasPeers: boolean
  hasReceipts: boolean
  hasStateBalance: boolean
  hasStateAccount: boolean
  hasStateCode: boolean
  hasRichList: boolean
  hasTotalSupply: boolean
}

export class RpcChainClient implements ChainClient {
  private capabilities: Capabilities | null = null

  constructor(private rpc: RpcClient) {}

  /**
   * Detect available RPC methods.
   * This method is memoized - capabilities are detected once and cached.
   */
  async detectCapabilities(): Promise<Capabilities> {
    if (this.capabilities) {
      return this.capabilities
    }

    log.info('Detecting node capabilities...')

    const checks = await Promise.allSettled([
      this.rpc.call('mempool.getPending', []),
      this.rpc.call('p2p.getPeers', []),
      this.rpc.call('receipt.getReceipt', ['0x0000000000000000000000000000000000000000000000000000000000000000']),
      this.rpc.call('state.getBalance', ['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4']),
      this.rpc.call('state.getAccount', ['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4']),
      this.rpc.call('state.getCode', ['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4']),
      this.rpc.call('state.getRichList', [10, 0]),
      this.rpc.call('state.getTotalSupply', [])
    ])

    // Helper to check if a method is truly available
    // A method is available if:
    // 1. The call succeeded (fulfilled), OR
    // 2. The call failed but NOT due to "method not found" (meaning the method exists but failed for other reasons like invalid params)
    const isMethodAvailable = (result: PromiseSettledResult<unknown>): boolean => {
      if (result.status === 'fulfilled') {
        return true
      }
      
      // For rejected calls, check if it's a "method not found" error
      // If it IS a "method not found" error, the method is NOT available
      // If it's any other error, the method exists but failed (so it IS available)
      const errorMsg = result.reason?.message?.toLowerCase() || ''
      const isMethodNotFoundError = 
        errorMsg.includes('method not found') ||
        errorMsg.includes('unknown method') ||
        errorMsg.includes('not implemented')
      
      // Return true (available) if it's NOT a "method not found" error
      return !isMethodNotFoundError
    }

    this.capabilities = {
      hasMempool: isMethodAvailable(checks[0]),
      hasPeers: isMethodAvailable(checks[1]),
      hasReceipts: isMethodAvailable(checks[2]),
      hasStateBalance: isMethodAvailable(checks[3]),
      hasStateAccount: isMethodAvailable(checks[4]),
      hasStateCode: isMethodAvailable(checks[5]),
      hasRichList: isMethodAvailable(checks[6]),
      hasTotalSupply: isMethodAvailable(checks[7])
    }

    log.info({ capabilities: this.capabilities }, 'Capabilities detected')
    return this.capabilities
  }

  async getHead(): Promise<unknown> {
    try {
      return await this.rpc.call('chain.getHead', [])
    } catch (error) {
      log.error({ error }, 'Failed to get head')
      throw new Error('Failed to get chain head from RPC')
    }
  }

  async getBlockByNumber(
    height: number | string,
    includeTxs = false,
    includeReceipts = false
  ): Promise<unknown> {
    try {
      // Normalize height parameter
      const normalizedHeight = typeof height === 'string' && height.startsWith('0x')
        ? parseInt(height.slice(2), 16)
        : height

      return await this.rpc.call('chain.getBlockByNumber', [
        normalizedHeight,
        includeTxs,
        includeReceipts
      ])
    } catch (error) {
      log.error({ height, error }, 'Failed to get block by number')
      throw new Error(`Failed to get block ${height} from RPC`)
    }
  }

  async getBlockByHash(
    hash: string,
    includeTxs = false,
    includeReceipts = false
  ): Promise<unknown> {
    try {
      return await this.rpc.call('chain.getBlockByHash', [
        hash,
        includeTxs,
        includeReceipts
      ])
    } catch (error) {
      log.error({ hash, error }, 'Failed to get block by hash')
      throw new Error(`Failed to get block ${hash} from RPC`)
    }
  }

  async getTransactionByHash(hash: string): Promise<unknown> {
    const normalizedHash = normalizeTxHash(hash)
    const methods = ['tx.getTransactionByHash', 'tx.getTransaction', 'chain.getTransactionByHash', 'chain.getTx', 'mempool.getTx']
    try {
      for (const method of methods) {
        try {
          // eslint-disable-next-line no-await-in-loop
          const out = await this.rpc.call(method, [normalizedHash])
          if (out) return out
        } catch (error) {
          const message = String((error as Error)?.message ?? error).toLowerCase()
          if (message.includes('method not found') || message.includes('unknown method')) continue
          log.warn({ hash: normalizedHash, method, error }, 'Failed transaction lookup method')
        }
      }
      return null
    } catch (error) {
      log.warn({ hash: normalizedHash, error }, 'Failed to get transaction by hash')
      return null
    }
  }

  async getTransactionReceipt(hash: string): Promise<unknown> {
    const normalizedHash = normalizeTxHash(hash)
    const caps = await this.detectCapabilities()
    if (!caps.hasReceipts) {
      return null
    }

    for (const method of ['tx.getTransactionReceipt', 'receipt.getReceipt', 'tx.getReceipt']) {
      try {
        // eslint-disable-next-line no-await-in-loop
        const out = await this.rpc.call(method, [normalizedHash])
        if (out) return out
      } catch (error) {
        const message = String((error as Error)?.message ?? error).toLowerCase()
        if (message.includes('method not found') || message.includes('unknown method')) continue
        log.warn({ hash: normalizedHash, method, error }, 'Failed receipt lookup method')
      }
    }
    return null
  }

  async getMempoolPending(): Promise<string[]> {
    const caps = await this.detectCapabilities()
    if (!caps.hasMempool) {
      return []
    }

    try {
      const result = await this.rpc.call<unknown>('mempool.getPending', [])
      if (Array.isArray(result)) {
        return result
          .map((entry) => {
            if (typeof entry === 'string') return entry
            if (entry && typeof entry === 'object') {
              const hash = (entry as Record<string, unknown>).hash ?? (entry as Record<string, unknown>).txHash
              return typeof hash === 'string' ? hash : null
            }
            return null
          })
          .filter((hash): hash is string => typeof hash === 'string' && hash.length > 0)
      }
      if (result && typeof result === 'object') {
        const record = result as Record<string, unknown>
        const candidates = [record.txs, record.pending, record.items]
        for (const candidate of candidates) {
          if (!Array.isArray(candidate)) continue
          return candidate
            .map((entry) => {
              if (typeof entry === 'string') return entry
              if (entry && typeof entry === 'object') {
                const hash = (entry as Record<string, unknown>).hash ?? (entry as Record<string, unknown>).txHash
                return typeof hash === 'string' ? hash : null
              }
              return null
            })
            .filter((hash): hash is string => typeof hash === 'string' && hash.length > 0)
        }
      }
      return []
    } catch (error) {
      log.warn({ error }, 'Failed to get mempool pending')
      return []
    }
  }

  async getMempoolStats(): Promise<{ count: number; totalBytes: number; oldestAgeSec: number | null }> {
    const caps = await this.detectCapabilities()
    if (!caps.hasMempool) {
      return { count: 0, totalBytes: 0, oldestAgeSec: null }
    }

    try {
      const result = await this.rpc.call<Record<string, unknown>>('mempool.getStats', [])

      return {
        count: toFiniteInt(result?.count ?? result?.size ?? result?.txCount) ?? 0,
        totalBytes: toFiniteInt(result?.totalBytes ?? result?.bytes ?? result?.total_bytes) ?? 0,
        oldestAgeSec: toFiniteInt(result?.oldestAgeSec ?? result?.oldest_age_sec) ?? null
      }
    } catch (error) {
      log.warn({ error }, 'Failed to get mempool stats')
      return { count: 0, totalBytes: 0, oldestAgeSec: null }
    }
  }

  async getPeers(): Promise<unknown[]> {
    const caps = await this.detectCapabilities()
    if (!caps.hasPeers) {
      return []
    }

    try {
      const result = await this.rpc.call<unknown[] | { peers?: unknown[] }>('p2p.getPeers', [])
      // Handle both array and object response
      if (Array.isArray(result)) {
        return result
      }
      if (result && typeof result === 'object' && 'peers' in result) {
        return (result as { peers?: unknown[] }).peers || []
      }
      return []
    } catch (error) {
      log.warn({ error }, 'Failed to get peers')
      return []
    }
  }

  async getBalance(address: string, tag: 'latest' | 'pending' = 'latest'): Promise<string> {
    const caps = await this.detectCapabilities()
    if (!caps.hasStateBalance) {
      return '0x0'
    }

    try {
      const result = await this.rpc.call<unknown>('state.getBalance', [address, tag])
      // Handle both string and object response
      if (typeof result === 'string') {
        return result
      }
      if (typeof result === 'number' && Number.isFinite(result)) {
        return `0x${Math.max(0, Math.floor(result)).toString(16)}`
      }
      if (typeof result === 'bigint') {
        return result >= 0n ? `0x${result.toString(16)}` : '0x0'
      }
      if (result && typeof result === 'object') {
        const record = result as Record<string, unknown>
        const candidate = record.balance ?? record.value ?? record.amount
        if (typeof candidate === 'string') return candidate
        if (typeof candidate === 'number' && Number.isFinite(candidate)) {
          return `0x${Math.max(0, Math.floor(candidate)).toString(16)}`
        }
        if (typeof candidate === 'bigint') {
          return candidate >= 0n ? `0x${candidate.toString(16)}` : '0x0'
        }
      }
      return '0x0'
    } catch (error) {
      log.warn({ address, tag, error }, 'Failed to get balance')
      return '0x0'
    }
  }

  async getAccount(address: string): Promise<unknown> {
    const caps = await this.detectCapabilities()
    if (!caps.hasStateAccount) {
      return null
    }

    for (const method of ['state.getAccount', 'state_getAccount', 'account.get']) {
      try {
        // eslint-disable-next-line no-await-in-loop
        const out = await this.rpc.call(method, [address])
        if (out) return out
      } catch (error) {
        const message = String((error as Error)?.message ?? error).toLowerCase()
        if (message.includes('method not found') || message.includes('unknown method')) continue
        log.warn({ address, method, error }, 'Failed account lookup method')
      }
    }
    return null
  }

  async getCode(address: string): Promise<string | null> {
    const caps = await this.detectCapabilities()
    if (!caps.hasStateCode) {
      return null
    }

    for (const method of ['state.getCode', 'state_getCode', 'state.getContractCode', 'eth_getCode']) {
      try {
        // eslint-disable-next-line no-await-in-loop
        const out = await this.rpc.call(method, [address])
        if (typeof out === 'string') return out
        if (out && typeof out === 'object') {
          const code = (out as Record<string, unknown>).code ?? (out as Record<string, unknown>).bytecode
          if (typeof code === 'string') return code
        }
      } catch (error) {
        const message = String((error as Error)?.message ?? error).toLowerCase()
        if (message.includes('method not found') || message.includes('unknown method')) continue
        log.warn({ address, method, error }, 'Failed code lookup method')
      }
    }
    return null
  }

  async getRichList(limit: number, offset: number): Promise<unknown> {
    const caps = await this.detectCapabilities()
    if (!caps.hasRichList) {
      throw new Error('Node does not support state.getRichList')
    }

    try {
      return await this.rpc.call('state.getRichList', [limit, offset])
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      log.warn({ limit, offset, error: message }, 'Failed to get rich list')
      throw new Error(`Failed to get rich list from RPC: ${message}`)
    }
  }

  async getTotalSupply(): Promise<unknown> {
    const caps = await this.detectCapabilities()
    if (!caps.hasTotalSupply) {
      throw new Error('Node does not support state.getTotalSupply')
    }

    try {
      return await this.rpc.call('state.getTotalSupply', [])
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      log.warn({ error: message }, 'Failed to get total supply')
      throw new Error(`Failed to get total supply from RPC: ${message}`)
    }
  }
}

function toFiniteInt(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return Math.floor(value)
  if (typeof value === 'bigint') return Number(value)
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed.length) return null
    const parsed = trimmed.startsWith('0x') ? Number.parseInt(trimmed.slice(2), 16) : Number.parseInt(trimmed, 10)
    if (Number.isNaN(parsed)) return null
    return parsed
  }
  return null
}
