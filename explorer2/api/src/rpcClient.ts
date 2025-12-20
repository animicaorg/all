import { JsonRpcClient } from '@animica/explorer2-shared'

export interface RpcOptions {
  url: string
}

export class RpcClient {
  private client: JsonRpcClient

  constructor(opts: RpcOptions) {
    this.client = new JsonRpcClient(opts.url)
  }

  async getHead(): Promise<unknown> {
    return this.client.request('chain.getHead', [])
  }

  async getBlockByNumber(height: number | string, includeTxs = false, includeReceipts = false): Promise<unknown> {
    return this.client.request('chain.getBlockByNumber', [height, includeTxs, includeReceipts])
  }

  async getBlockByHash(hash: string, includeTxs = false, includeReceipts = false): Promise<unknown> {
    return this.client.request('chain.getBlockByHash', [hash, includeTxs, includeReceipts])
  }

  async getTransactionByHash(hash: string): Promise<unknown> {
    return this.client.request('tx.getTransactionByHash', [hash])
  }

  async getTransactionReceipt(hash: string): Promise<unknown> {
    return this.client.request('tx.getTransactionReceipt', [hash])
  }

  async getMempoolPending(): Promise<string[]> {
    return this.client.request('mempool.getPending', [])
  }

  async getMempoolStats(): Promise<{ count: number; totalBytes: number; oldestAgeSec: number | null }> {
    return this.client.request('mempool.getStats', [])
  }

  async getPeers(): Promise<unknown[]> {
    return this.client.request('net.peers', [])
  }

  async getBalance(address: string, tag: 'latest' | 'pending' = 'latest'): Promise<string> {
    return this.client.request('state.getBalance', [address, tag])
  }
}
