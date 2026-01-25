/**
 * Animica RPC Client
 * 
 * Robust JSON-RPC client with:
 * - Timeouts
 * - Retry with exponential backoff
 * - Typed responses and error mapping
 * - Feature detection
 */

import axios, { AxiosInstance } from "axios";
import type { Logger } from "pino";
import {
  RpcError,
  LocalRpcError,
  MethodNotFoundError,
  InvalidParamsError,
  TimeoutError,
  NodeUnavailableError,
} from "./errors.js";
import { retryWithBackoff } from "./retry.js";
import type {
  RpcRequest,
  RpcResponse,
  BlockInfo,
  TransactionInfo,
  ChainHead,
  FeeEstimate,
  RpcCapabilities,
} from "./types.js";

export interface AnimicaRpcClientOptions {
  url: string;
  timeout: number;
  maxRetries: number;
  retryDelay: number;
  logger: Logger;
}

export class AnimicaRpcClient {
  private client: AxiosInstance;
  private requestId: number = 0;
  private capabilities: RpcCapabilities | null = null;
  
  constructor(private options: AnimicaRpcClientOptions) {
    this.client = axios.create({
      baseURL: options.url,
      timeout: options.timeout,
      headers: {
        "Content-Type": "application/json",
      },
    });
  }
  
  /**
   * Detect RPC capabilities by attempting known methods
   */
  async detectCapabilities(): Promise<RpcCapabilities> {
    this.options.logger.info("Detecting Animica RPC capabilities");
    
    const capabilities: RpcCapabilities = {
      supportsGetHead: false,
      supportsGetBlockByHeight: false,
      supportsGetBlockByHash: false,
      supportsGetTransaction: false,
      supportsSendRawTransaction: false,
      supportsWalletCreateAddress: false,
      supportsWalletSend: false,
      supportsEstimateFee: false,
    };
    
    // Test common method names
    const tests = [
      { method: "chain.getHead", key: "supportsGetHead" as keyof RpcCapabilities },
      { method: "chain.getBlockByHeight", key: "supportsGetBlockByHeight" as keyof RpcCapabilities },
      { method: "chain.getBlockByHash", key: "supportsGetBlockByHash" as keyof RpcCapabilities },
      { method: "tx.get", key: "supportsGetTransaction" as keyof RpcCapabilities },
      { method: "tx.sendRaw", key: "supportsSendRawTransaction" as keyof RpcCapabilities },
      { method: "wallet.createAddress", key: "supportsWalletCreateAddress" as keyof RpcCapabilities },
      { method: "wallet.send", key: "supportsWalletSend" as keyof RpcCapabilities },
      { method: "tx.estimateFee", key: "supportsEstimateFee" as keyof RpcCapabilities },
    ];
    
    for (const test of tests) {
      try {
        // Try with empty/dummy params - we only care if method exists
        await this.call(test.method, []);
        capabilities[test.key] = true;
      } catch (error) {
        // Method not found = not supported
        if (error instanceof MethodNotFoundError) {
          capabilities[test.key] = false;
        } else if (error instanceof InvalidParamsError) {
          // Invalid params means the method exists but we didn't call it correctly
          capabilities[test.key] = true;
        } else {
          // Other errors - assume not supported
          capabilities[test.key] = false;
        }
      }
    }
    
    this.capabilities = capabilities;
    this.options.logger.info({ capabilities }, "RPC capabilities detected");
    
    return capabilities;
  }
  
  /**
   * Raw JSON-RPC call with retry logic
   */
  private async call<T = any>(method: string, params: any[] = []): Promise<T> {
    const correlationId = `rpc-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    
    return retryWithBackoff(
      async () => {
        const request: RpcRequest = {
          jsonrpc: "2.0",
          method,
          params,
          id: ++this.requestId,
        };
        
        this.options.logger.debug(
          { method, params: params.length, correlationId, requestId: request.id },
          "RPC request"
        );
        
        try {
          const response = await this.client.post<RpcResponse<T>>("", request);
          
          if (response.data.error) {
            const { error } = response.data;
            this.options.logger.warn(
              { error, method, correlationId },
              "RPC error response"
            );
            
            // Map error codes to specific error types
            if (error.code === -32601) {
              throw new MethodNotFoundError(method);
            } else if (error.code === -32602) {
              throw new InvalidParamsError(error.message);
            } else {
              throw new RpcError(error.message, error.code, error.data);
            }
          }
          
          this.options.logger.debug(
            { method, correlationId, requestId: request.id },
            "RPC response received"
          );
          
          return response.data.result as T;
        } catch (error: any) {
          // Axios errors
          if (error.code === "ECONNREFUSED") {
            throw new NodeUnavailableError("Node connection refused", error);
          } else if (error.code === "ETIMEDOUT" || error.code === "ECONNABORTED") {
            throw new TimeoutError(method, this.options.timeout);
          } else if (error instanceof RpcError) {
            throw error;
          } else {
            throw new LocalRpcError(`RPC call failed: ${error.message}`, error);
          }
        }
      },
      {
        maxRetries: this.options.maxRetries,
        baseDelay: this.options.retryDelay,
        jitter: true,
      },
      this.options.logger,
      `RPC ${method}`
    );
  }
  
  /**
   * Get current chain head
   */
  async getHead(): Promise<ChainHead> {
    const result = await this.call<any>("chain.getHead");
    return {
      height: Number(result.height),
      hash: result.hash,
    };
  }
  
  /**
   * Get block by height
   */
  async getBlockByHeight(height: number): Promise<BlockInfo> {
    const result = await this.call<any>("chain.getBlockByHeight", [height]);
    return this.normalizeBlockInfo(result);
  }
  
  /**
   * Get block by hash
   */
  async getBlockByHash(hash: string): Promise<BlockInfo> {
    const result = await this.call<any>("chain.getBlockByHash", [hash]);
    return this.normalizeBlockInfo(result);
  }
  
  /**
   * Get transaction by ID
   */
  async getTransaction(txid: string): Promise<TransactionInfo> {
    const result = await this.call<any>("tx.get", [txid]);
    return this.normalizeTransactionInfo(result);
  }
  
  /**
   * Send raw transaction
   */
  async sendRawTransaction(rawTx: string): Promise<string> {
    const result = await this.call<{ txid: string }>("tx.sendRaw", [rawTx]);
    return result.txid;
  }
  
  /**
   * Create a new address (if wallet supports it)
   */
  async createAddress(label?: string): Promise<string> {
    const result = await this.call<{ address: string }>("wallet.createAddress", label ? [label] : []);
    return result.address;
  }
  
  /**
   * Send to address (if wallet supports it)
   */
  async walletSend(to: string, amount: string, fee?: string): Promise<string> {
    const params: any[] = [to, amount];
    if (fee) params.push(fee);
    
    const result = await this.call<{ txid: string }>("wallet.send", params);
    return result.txid;
  }
  
  /**
   * Estimate fee
   */
  async estimateFee(): Promise<FeeEstimate> {
    const result = await this.call<any>("tx.estimateFee");
    return {
      gas_price: result.gas_price || result.gasPrice,
      estimated_fee: result.estimated_fee || result.estimatedFee,
    };
  }
  
  /**
   * Check node health
   */
  async health(): Promise<boolean> {
    try {
      await this.getHead();
      return true;
    } catch {
      return false;
    }
  }
  
  /**
   * Normalize block info from various formats
   */
  private normalizeBlockInfo(block: any): BlockInfo {
    return {
      height: Number(block.height || block.number),
      hash: block.hash || block.block_hash,
      parent_hash: block.parent_hash || block.parentHash || block.prev_hash,
      timestamp: Number(block.timestamp || block.time),
      txs: block.txs || block.transactions || [],
    };
  }
  
  /**
   * Normalize transaction info from various formats
   */
  private normalizeTransactionInfo(tx: any): TransactionInfo {
    return {
      txid: tx.txid || tx.hash,
      from: tx.from || tx.sender,
      to: tx.to || tx.recipient,
      value: tx.value || tx.amount || "0",
      nonce: Number(tx.nonce || 0),
      gas_limit: Number(tx.gas_limit || tx.gasLimit || 0),
      gas_price: tx.gas_price || tx.gasPrice || "0",
      block_height: tx.block_height !== undefined ? Number(tx.block_height) : undefined,
      block_hash: tx.block_hash || tx.blockHash,
      confirmations: tx.confirmations !== undefined ? Number(tx.confirmations) : undefined,
      status: tx.status,
    };
  }
}

/**
 * Create an Animica RPC client
 */
export function createAnimicaRpcClient(options: AnimicaRpcClientOptions): AnimicaRpcClient {
  return new AnimicaRpcClient(options);
}
