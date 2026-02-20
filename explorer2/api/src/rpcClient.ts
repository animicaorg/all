/**
 * JSON-RPC 2.0 client for Animica node.
 * Implements retries, timeouts, URL normalisation, and 405 detection.
 */

import pino from 'pino'
import { classifyHttpError, normalizeRpcUrl } from './urlNormalizer.js'

const log = pino({ name: 'rpc-client' })

export interface RpcClientConfig {
  url: string
  timeout?: number
  maxRetries?: number
  retryDelay?: number
}

export class RpcError extends Error {
  constructor(
    message: string,
    public code?: number,
    public data?: unknown
  ) {
    super(message)
    this.name = 'RpcError'
  }
}

export class RpcTimeoutError extends RpcError {
  constructor(method: string, timeout: number) {
    super(`RPC timeout after ${timeout}ms: ${method}`)
    this.name = 'RpcTimeoutError'
  }
}

export class RpcClient {
  private url: string
  private timeout: number
  private maxRetries: number
  private retryDelay: number
  private requestId = 0

  constructor(config: RpcClientConfig) {
    const norm = normalizeRpcUrl(config.url)
    if (norm.wasNormalized) {
      log.info({ original: config.url, normalized: norm.url, note: norm.note }, 'RPC URL normalized')
    }
    this.url = norm.url
    this.timeout = config.timeout ?? 30000 // 30s default
    this.maxRetries = config.maxRetries ?? 3
    this.retryDelay = config.retryDelay ?? 1000
  }

  /**
   * Make a JSON-RPC call with retries and timeout.
   */
  async call<T = unknown>(method: string, params: unknown[] = []): Promise<T> {
    const id = ++this.requestId
    const payload = {
      jsonrpc: '2.0',
      id,
      method,
      params
    }

    let lastError: Error | null = null
    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        return await this.executeRequest<T>(payload)
      } catch (error) {
        lastError = error as Error
        
        // Don't retry on certain errors
        if (error instanceof RpcError) {
          // Method not found, invalid params, etc. - don't retry
          if (error.code && error.code < 0) {
            throw error
          }
        }
        
        if (attempt < this.maxRetries) {
          const delay = this.retryDelay * Math.pow(2, attempt)
          log.warn({ method, attempt, delay }, 'RPC call failed, retrying...')
          await sleep(delay)
        }
      }
    }

    throw lastError || new Error(`RPC call failed after ${this.maxRetries + 1} attempts`)
  }

  /**
   * Execute a single request with timeout.
   */
  private async executeRequest<T>(payload: unknown): Promise<T> {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), this.timeout)

    try {
      const response = await fetch(this.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload),
        signal: controller.signal
      })

      clearTimeout(timeoutId)

      if (!response.ok) {
        const classified = classifyHttpError(response.status, response.statusText)
        throw new RpcError(
          `HTTP ${response.status}: ${classified.hint}`,
          response.status,
          { kind: classified.kind, remediation: classified.remediation }
        )
      }

      const result = await response.json() as {
        jsonrpc: string
        id: number
        result?: T
        error?: { message: string; code?: number; data?: unknown }
      }

      if (result.error) {
        throw new RpcError(
          result.error.message || 'RPC error',
          result.error.code,
          result.error.data
        )
      }

      return result.result as T
    } catch (error) {
      clearTimeout(timeoutId)

      if (error instanceof RpcError) {
        throw error
      }

      if ((error as Error).name === 'AbortError') {
        const method = (payload as { method?: string }).method || 'unknown'
        throw new RpcTimeoutError(method, this.timeout)
      }

      throw new RpcError(
        error instanceof Error ? error.message : String(error)
      )
    }
  }

  /**
   * Test connectivity by calling a simple method.
   */
  async ping(): Promise<boolean> {
    try {
      await this.call('chain.getChainId', [])
      return true
    } catch {
      return false
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}
