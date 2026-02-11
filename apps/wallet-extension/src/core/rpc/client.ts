// RPC client for Animica nodes

export class RpcClient {
  private urls: string[];
  private currentIndex: number = 0;
  private failedUrls: Set<string> = new Set();

  constructor(urls: string[]) {
    this.urls = urls;
  }

  async call(method: string, params: any[] = []): Promise<any> {
    let lastError: Error | null = null;

    for (let i = 0; i < this.urls.length; i++) {
      const url = this.urls[this.currentIndex];
      
      if (this.failedUrls.has(url)) {
        this.currentIndex = (this.currentIndex + 1) % this.urls.length;
        continue;
      }

      try {
        const response = await fetch(url, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            jsonrpc: '2.0',
            id: Date.now(),
            method,
            params,
          }),
        });

        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const json = await response.json();

        if (json.error) {
          throw new Error(json.error.message || 'RPC error');
        }

        // Success - clear failed status
        this.failedUrls.delete(url);
        return json.result;
      } catch (error) {
        lastError = error as Error;
        this.failedUrls.add(url);
        this.currentIndex = (this.currentIndex + 1) % this.urls.length;
      }
    }

    throw new Error(
      `All RPC endpoints failed. Last error: ${lastError?.message || 'Unknown'}`
    );
  }

  async getBalance(address: string, tag: string = 'latest'): Promise<string> {
    return this.call('state.getBalance', [address, tag]);
  }

  async getNonce(address: string, tag: string = 'latest'): Promise<number> {
    return this.call('state.getNonce', [address, tag]);
  }

  async sendRawTransaction(rawTx: string): Promise<string> {
    return this.call('tx.sendRawTransaction', [rawTx]);
  }

  async getTransaction(txid: string): Promise<any> {
    return this.call('tx.getTransaction', [txid]);
  }

  async getTransactionStatus(txid: string): Promise<any> {
    return this.call('tx.getTransactionStatus', [txid]);
  }

  async getTransactionReceipt(txid: string): Promise<any> {
    return this.call('tx.getTransactionReceipt', [txid]);
  }

  async getChainId(): Promise<number> {
    return this.call('chain.getChainId', []);
  }

  async getHead(): Promise<any> {
    return this.call('chain.getHead', []);
  }

  async getBlock(blockId: string | number, full: boolean = false): Promise<any> {
    return this.call('block.getBlock', [blockId, full]);
  }

  async getMempoolStats(): Promise<any> {
    return this.call('tx2.getMempoolStats', []);
  }

  async listMempool(): Promise<string[]> {
    return this.call('mempool.list', []);
  }

  resetFailures(): void {
    this.failedUrls.clear();
  }

  getActiveUrl(): string {
    return this.urls[this.currentIndex];
  }
}
