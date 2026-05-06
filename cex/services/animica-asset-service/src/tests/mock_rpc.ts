/**
 * Mock Animica RPC Server for Testing
 * 
 * Simulates an Animica node with:
 * - Deterministic block generation
 * - Transaction tracking
 * - Reorg simulation
 * - Configurable delays and errors
 */

import type { BlockInfo, TransactionInfo, ChainHead } from "../rpc/types.js";

export interface MockBlock {
  height: number;
  hash: string;
  parent_hash: string;
  timestamp: number;
  txs: string[];
}

export interface MockTransaction {
  txid: string;
  from: string;
  to: string;
  value: string;
  nonce: number;
  gas_limit: number;
  gas_price: string;
  block_height?: number;
  block_hash?: string;
  status: "pending" | "confirmed";
}

export class MockAnimicaRpc {
  private blocks: Map<number, MockBlock> = new Map();
  private blocksByHash: Map<string, MockBlock> = new Map();
  private transactions: Map<string, MockTransaction> = new Map();
  private nonces: Map<string, number> = new Map();
  private balances: Map<string, bigint> = new Map();
  private currentHeight: number = 0;
  
  constructor() {
    // Genesis block
    this.addBlock(0, "genesis", "0x0000000000000000000000000000000000000000000000000000000000000000");
  }
  
  /**
   * Add a new block
   */
  addBlock(height: number, hash: string, parentHash: string, txs: string[] = []): MockBlock {
    const block: MockBlock = {
      height,
      hash,
      parent_hash: parentHash,
      timestamp: Date.now(),
      txs,
    };
    
    this.blocks.set(height, block);
    this.blocksByHash.set(hash, block);
    this.currentHeight = Math.max(this.currentHeight, height);
    
    // Mark transactions as confirmed
    for (const txid of txs) {
      const tx = this.transactions.get(txid);
      if (tx) {
        tx.block_height = height;
        tx.block_hash = hash;
        tx.status = "confirmed";
      }
    }
    
    return block;
  }
  
  /**
   * Mine next block
   */
  mineBlock(txids: string[] = []): MockBlock {
    const height = this.currentHeight + 1;
    const parentHash = this.blocks.get(this.currentHeight)!.hash;
    const hash = `0x${height.toString(16).padStart(64, '0')}`;
    
    return this.addBlock(height, hash, parentHash, txids);
  }
  
  /**
   * Add a transaction
   */
  addTransaction(tx: Omit<MockTransaction, "status">): MockTransaction {
    const fullTx: MockTransaction = {
      ...tx,
      status: "pending",
    };
    
    this.transactions.set(tx.txid, fullTx);
    
    // Update nonce
    this.nonces.set(tx.from, Math.max(this.nonces.get(tx.from) || 0, tx.nonce + 1));
    
    return fullTx;
  }

  setConfirmedBalance(address: string, amountAtoms: string): void {
    this.balances.set(address.toLowerCase(), BigInt(amountAtoms));
  }
  
  /**
   * Simulate a reorg by replacing blocks
   */
  simulateReorg(fromHeight: number, newBlocks: Array<{ hash: string; txs?: string[] }>): void {
    // Remove old blocks
    for (let h = fromHeight; h <= this.currentHeight; h++) {
      const oldBlock = this.blocks.get(h);
      if (oldBlock) {
        this.blocksByHash.delete(oldBlock.hash);
        this.blocks.delete(h);
        
        // Mark txs as pending again
        for (const txid of oldBlock.txs) {
          const tx = this.transactions.get(txid);
          if (tx) {
            tx.status = "pending";
            tx.block_height = undefined;
            tx.block_hash = undefined;
          }
        }
      }
    }
    
    // Add new blocks
    let parentHash = this.blocks.get(fromHeight - 1)!.hash;
    for (let i = 0; i < newBlocks.length; i++) {
      const height = fromHeight + i;
      const { hash, txs = [] } = newBlocks[i];
      this.addBlock(height, hash, parentHash, txs);
      parentHash = hash;
    }
  }
  
  /**
   * Get next nonce for address
   */
  getNonce(address: string): number {
    return this.nonces.get(address) || 0;
  }
  
  /**
   * RPC handlers
   */
  
  async getHead(): Promise<ChainHead> {
    const block = this.blocks.get(this.currentHeight)!;
    return {
      height: this.currentHeight,
      hash: block.hash,
    };
  }
  
  async getBlockByHeight(height: number): Promise<BlockInfo> {
    const block = this.blocks.get(height);
    if (!block) {
      throw new Error(`Block not found at height ${height}`);
    }
    
    return {
      height: block.height,
      hash: block.hash,
      parent_hash: block.parent_hash,
      timestamp: block.timestamp,
      txs: block.txs,
    };
  }
  
  async getBlockByHash(hash: string): Promise<BlockInfo> {
    const block = this.blocksByHash.get(hash);
    if (!block) {
      throw new Error(`Block not found with hash ${hash}`);
    }
    
    return {
      height: block.height,
      hash: block.hash,
      parent_hash: block.parent_hash,
      timestamp: block.timestamp,
      txs: block.txs,
    };
  }
  
  async getTransaction(txid: string): Promise<TransactionInfo> {
    const tx = this.transactions.get(txid);
    if (!tx) {
      throw new Error(`Transaction not found: ${txid}`);
    }
    
    return {
      txid: tx.txid,
      from: tx.from,
      to: tx.to,
      value: tx.value,
      nonce: tx.nonce,
      gas_limit: tx.gas_limit,
      gas_price: tx.gas_price,
      block_height: tx.block_height,
      block_hash: tx.block_hash,
      confirmations: tx.block_height !== undefined 
        ? this.currentHeight - tx.block_height + 1 
        : 0,
      status: tx.status,
    };
  }
  
  async sendRawTransaction(rawTx: string): Promise<string> {
    // Parse raw tx (simplified - in reality would decode hex)
    const txid = `0x${Math.random().toString(36).slice(2)}`;
    
    // For testing, we'll accept a simple JSON format
    try {
      const parsed = JSON.parse(rawTx);
      this.addTransaction({
        txid,
        from: parsed.from,
        to: parsed.to,
        value: parsed.value,
        nonce: parsed.nonce,
        gas_limit: parsed.gas_limit || 21000,
        gas_price: parsed.gas_price || "1000000000",
      });
    } catch {
      // Invalid format - still return txid
    }
    
    return txid;
  }
  
  async createAddress(label?: string): Promise<string> {
    const address = `anim1${Math.random().toString(36).slice(2)}`;
    return address;
  }
  
  async walletSend(to: string, amount: string, fee?: string): Promise<string> {
    const txid = `0x${Math.random().toString(36).slice(2)}`;
    
    this.addTransaction({
      txid,
      from: "wallet_address",
      to,
      value: amount,
      nonce: this.getNonce("wallet_address"),
      gas_limit: 21000,
      gas_price: fee || "1000000000",
    });
    
    return txid;
  }
  
  async estimateFee(): Promise<{ gas_price: string; estimated_fee: string }> {
    return {
      gas_price: "1000000000",
      estimated_fee: "21000000000000", // 21000 * 1 gwei
    };
  }

  async getPendingTransactionIds(): Promise<string[]> {
    return Array.from(this.transactions.values())
      .filter((tx) => tx.status === "pending")
      .map((tx) => tx.txid);
  }

  async getMempoolTransaction(txid: string): Promise<TransactionInfo> {
    return this.getTransaction(txid);
  }

  async getConfirmedAddressBalance(address: string): Promise<string> {
    return (this.balances.get(address.toLowerCase()) ?? 0n).toString();
  }
}

/**
 * Create a mock RPC client that uses MockAnimicaRpc
 */
export function createMockRpcClient(mockRpc: MockAnimicaRpc): any {
  return {
    getHead: () => mockRpc.getHead(),
    getBlockByHeight: (height: number) => mockRpc.getBlockByHeight(height),
    getBlockByHash: (hash: string) => mockRpc.getBlockByHash(hash),
    getTransaction: (txid: string) => mockRpc.getTransaction(txid),
    sendRawTransaction: (rawTx: string) => mockRpc.sendRawTransaction(rawTx),
    createAddress: (label?: string) => mockRpc.createAddress(label),
    walletSend: (to: string, amount: string, fee?: string) => mockRpc.walletSend(to, amount, fee),
    estimateFee: () => mockRpc.estimateFee(),
    getPendingTransactionIds: () => mockRpc.getPendingTransactionIds(),
    getMempoolTransaction: (txid: string) => mockRpc.getMempoolTransaction(txid),
    getConfirmedAddressBalance: (address: string) => mockRpc.getConfirmedAddressBalance(address),
    health: async () => true,
  };
}
