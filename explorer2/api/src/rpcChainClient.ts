/**
 * RPC-based ChainClient implementation.
 * Connects directly to the Animica node's JSON-RPC endpoint.
 */

import { RpcClient } from './rpcClient.js'
import type { ChainClient } from './service.js'
import pino from 'pino'

const log = pino({ name: 'rpc-chain-client' })

/**
 * Capabilities detected from the node.
 */
interface Capabilities {
  hasMempool: boolean
  hasPeers: boolean
  hasReceipts: boolean
  hasStateBalance: boolean
}

export class RpcChainClient implements ChainClient {
  private capabilities: Capabilities | null = null

  constructor(private rpc: RpcClient) {}

  /**
   * Detect available RPC methods.
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
      this.rpc.call('state.getBalance', ['anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq5nvly4'])
    ])

    this.capabilities = {
      hasMempool: checks[0].status === 'fulfilled' || (checks[0].status === 'rejected' && !checks[0].reason?.message?.includes('not found')),
      hasPeers: checks[1].status === 'fulfilled' || (checks[1].status === 'rejected' && !checks[1].reason?.message?.includes('not found')),
      hasReceipts: checks[2].status === 'fulfilled' || (checks[2].status === 'rejected' && !checks[2].reason?.message?.includes('not found')),
      hasStateBalance: checks[3].status === 'fulfilled' || (checks[3].status === 'rejected' && !checks[3].reason?.message?.includes('not found'))
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
    try {
      return await this.rpc.call('tx.getTransaction', [hash])
    } catch (error) {
      log.warn({ hash, error }, 'Failed to get transaction by hash')
      // Some nodes might not have tx index, return null instead of throwing
      return null
    }
  }

  async getTransactionReceipt(hash: string): Promise<unknown> {
    const caps = await this.detectCapabilities()
    if (!caps.hasReceipts) {
      return null
    }

    try {
      return await this.rpc.call('receipt.getReceipt', [hash])
    } catch (error) {
      log.warn({ hash, error }, 'Failed to get receipt')
      return null
    }
  }

  async getMempoolPending(): Promise<string[]> {
    const caps = await this.detectCapabilities()
    if (!caps.hasMempool) {
      return []
    }

    try {
      const result = await this.rpc.call<string[] | { txs?: string[] }>('mempool.getPending', [])
      // Handle both array and object response
      if (Array.isArray(result)) {
        return result
      }
      if (result && typeof result === 'object' && 'txs' in result) {
        return result.txs || []
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
      const result = await this.rpc.call<{
        count?: number
        totalBytes?: number
        oldestAgeSec?: number | null
      }>('mempool.getStats', [])

      return {
        count: result?.count ?? 0,
        totalBytes: result?.totalBytes ?? 0,
        oldestAgeSec: result?.oldestAgeSec ?? null
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
      const result = await this.rpc.call<string | { balance?: string }>('state.getBalance', [address, tag])
      // Handle both string and object response
      if (typeof result === 'string') {
        return result
      }
      if (result && typeof result === 'object' && 'balance' in result) {
        return result.balance || '0x0'
      }
      return '0x0'
    } catch (error) {
      log.warn({ address, tag, error }, 'Failed to get balance')
      return '0x0'
    }
  }
}
