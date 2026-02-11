/**
 * RPC Client wrapper for Animica JSON-RPC
 */

export interface RPCRequest {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: any[];
}

export interface RPCResponse<T = any> {
  jsonrpc: "2.0";
  id: number | string;
  result?: T;
  error?: {
    code: number;
    message: string;
    data?: any;
  };
}

export interface RPCClientConfig {
  url: string;
  timeout?: number;
}

export class RPCClient {
  private url: string;
  private timeout: number;
  private requestId: number;

  constructor(config: RPCClientConfig) {
    this.url = config.url;
    this.timeout = config.timeout || 30000;
    this.requestId = 0;
  }

  async request<T = any>(method: string, params?: any[]): Promise<T> {
    const id = ++this.requestId;
    const body: RPCRequest = {
      jsonrpc: "2.0",
      id,
      method,
      params: params || [],
    };

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.timeout);

    try {
      const response = await fetch(this.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const data: RPCResponse<T> = await response.json();

      if (data.error) {
        throw new Error(`RPC Error ${data.error.code}: ${data.error.message}`);
      }

      return data.result as T;
    } catch (error: any) {
      clearTimeout(timeoutId);
      if (error.name === "AbortError") {
        throw new Error("Request timeout");
      }
      throw error;
    }
  }

  // Chain methods
  async getHeight(): Promise<number> {
    return this.request("chain.getHeight");
  }

  async getBlock(height: number): Promise<any> {
    return this.request("chain.getBlock", [height]);
  }

  async getHead(): Promise<any> {
    return this.request("chain.getHead");
  }

  // Transaction methods
  async sendTransaction(signedTx: string): Promise<string> {
    return this.request("tx.send", [signedTx]);
  }

  async getReceipt(txHash: string): Promise<any> {
    return this.request("tx.getReceipt", [txHash]);
  }

  async estimateGas(tx: any): Promise<number> {
    return this.request("tx.estimateGas", [tx]);
  }

  // Contract methods
  async contractCall(params: any): Promise<any> {
    return this.request("contract.call", [params]);
  }

  async simulateContract(params: any): Promise<any> {
    return this.request("contract.simulate", [params]);
  }
}

/**
 * Create a new RPC client instance
 */
export function createRPCClient(url: string, timeout?: number): RPCClient {
  return new RPCClient({ url, timeout });
}
