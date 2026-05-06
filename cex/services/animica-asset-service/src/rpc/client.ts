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
      supportsMempoolGetPending: false,
      supportsMempoolGet: false,
      supportsStateGetAddressBalance: false,
      supportsStateGetBalance: false,
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
      { method: "mempool.getPending", key: "supportsMempoolGetPending" as keyof RpcCapabilities },
      { method: "mempool.get", key: "supportsMempoolGet" as keyof RpcCapabilities },
      { method: "state.getAddressBalance", key: "supportsStateGetAddressBalance" as keyof RpcCapabilities },
      { method: "state.getBalance", key: "supportsStateGetBalance" as keyof RpcCapabilities },
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
  async call<T = any>(method: string, params: any[] = []): Promise<T> {
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
   * Get pending mempool transaction hashes.
   *
   * The current Animica RPC exposes a global mempool hash list, not an
   * address-indexed mempool query. The scanner filters this bounded list
   * locally against assigned deposit addresses.
   */
  async getPendingTransactionIds(): Promise<string[]> {
    const result = await this.call<any>("mempool.getPending");
    const values = Array.isArray(result)
      ? result
      : Array.isArray(result?.txids)
        ? result.txids
        : Array.isArray(result?.transactions)
          ? result.transactions
          : [];

    return values
      .map((value: any) => {
        if (typeof value === "string") return value;
        return value?.txid || value?.hash || value?.tx?.hash || null;
      })
      .filter((value: unknown): value is string => typeof value === "string" && value.length > 0);
  }

  /**
   * Get a pending transaction from the mempool. Falls back to tx.get because
   * the live RPC also returns pending transactions from that method.
   */
  async getMempoolTransaction(txid: string): Promise<TransactionInfo> {
    try {
      const result = await this.call<any>("mempool.get", [txid]);
      return this.normalizeTransactionInfo(result);
    } catch (error) {
      if (error instanceof MethodNotFoundError || error instanceof InvalidParamsError) {
        return this.getTransaction(txid);
      }
      throw error;
    }
  }

  /**
   * Get confirmed on-chain balance for an address.
   *
   * This is used as a conservative historical fallback when confirmed
   * transaction lookups expose only hash/block metadata. It never includes
   * pending incoming amounts in the credited value.
   */
  async getConfirmedAddressBalance(address: string): Promise<string> {
    try {
      const result = await this.call<any>("state.getAddressBalance", [address]);
      return this.normalizeBalanceAtoms(
        result?.confirmed_balance ?? result?.confirmedBalance ?? result?.balance ?? result
      );
    } catch (error) {
      if (!(error instanceof MethodNotFoundError || error instanceof InvalidParamsError)) {
        throw error;
      }
    }

    const result = await this.call<any>("state.getBalance", [address]);
    return this.normalizeBalanceAtoms(
      result?.confirmed_balance ?? result?.confirmedBalance ?? result?.balance ?? result
    );
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
    const source = block?.header || block;
    const transactions = block?.txs || block?.transactions || source?.txs || source?.transactions || [];
    const txs = Array.isArray(transactions)
      ? transactions
          .map((tx: any) => {
            if (typeof tx === "string") return tx;
            return tx?.txid || tx?.hash || tx?.tx?.hash || null;
          })
          .filter((txid: unknown): txid is string => typeof txid === "string" && txid.length > 0)
      : [];

    return {
      height: Number(source.height ?? source.number),
      hash: source.hash || source.block_hash,
      parent_hash: source.parent_hash || source.parentHash || source.prev_hash,
      timestamp: Number(source.timestamp ?? source.time ?? 0),
      txs,
    };
  }
  
  /**
   * Normalize transaction info from various formats
   */
  private normalizeTransactionInfo(tx: any): TransactionInfo {
    const source = tx?.tx || tx?.transaction || tx;
    const txid = tx?.txid || tx?.hash || source?.txid || source?.hash;
    const value = source?.value ?? source?.amount ?? tx?.value ?? tx?.amount ?? "0";

    return {
      txid,
      from: source?.from || source?.sender || "",
      to: source?.to || source?.recipient || "",
      value: String(value),
      nonce: Number(source?.nonce || 0),
      gas_limit: Number(source?.gas_limit || source?.gasLimit || source?.gas || 0),
      gas_price: String(source?.gas_price || source?.gasPrice || source?.maxFee || source?.tip || "0"),
      block_height: tx.block_height !== undefined ? Number(tx.block_height) : undefined,
      block_hash: tx.block_hash || tx.blockHash,
      confirmations: tx.confirmations !== undefined ? Number(tx.confirmations) : undefined,
      status: tx.status,
    };
  }

  private normalizeBalanceAtoms(value: any): string {
    if (typeof value === "number") {
      if (!Number.isSafeInteger(value) || value < 0) {
        throw new Error(`Invalid balance value: ${String(value)}`);
      }
      return String(value);
    }

    if (typeof value !== "string") {
      throw new Error(`Invalid balance value: ${String(value)}`);
    }

    const trimmed = value.trim();
    if (/^0x[0-9a-fA-F]+$/.test(trimmed)) {
      return BigInt(trimmed).toString();
    }
    if (/^\d+$/.test(trimmed)) {
      return trimmed;
    }

    throw new Error(`Invalid balance value: ${value}`);
  }
}

/**
 * Create an Animica RPC client
 */
export function createAnimicaRpcClient(options: AnimicaRpcClientOptions): AnimicaRpcClient {
  return new AnimicaRpcClient(options);
}
