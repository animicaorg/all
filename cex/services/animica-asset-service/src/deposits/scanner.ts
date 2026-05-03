/**
 * Block Scanner
 * 
 * Scans Animica blockchain for deposits with:
 * - Leader election via DB lock
 * - Reorg safety via parent hash verification
 * - Confirmation tracking
 * - Idempotent deposit creation
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { ScanStateRepository } from "../db/repositories/scan_state_repo.js";
import { BlocksRepository } from "../db/repositories/blocks_repo.js";
import { DepositsRepository } from "../db/repositories/deposits_repo.js";
import { SeenTxsRepository } from "../db/repositories/seen_txs_repo.js";
import { AddressesRepository } from "../db/repositories/addresses_repo.js";
import { TransactionParser } from "./parser.js";
import { ReorgHandler } from "./reorg.js";
import { withTransaction } from "../db/tx.js";

export interface ScannerConfig {
  assetNetworkId: string;
  confirmationsRequired: number;
  scanBatch: number;
  maxReorgDepth: number;
  walletId: string; // dummy wallet ID for ANIMICA_NODE provider
}

export class BlockScanner {
  private scanStateRepo: ScanStateRepository;
  private blocksRepo: BlocksRepository;
  private depositsRepo: DepositsRepository;
  private seenTxsRepo: SeenTxsRepository;
  private addressesRepo: AddressesRepository;
  private parser: TransactionParser;
  private reorgHandler: ReorgHandler;
  
  constructor(
    private pool: Pool,
    private rpcClient: AnimicaRpcClient,
    private config: ScannerConfig,
    private logger: Logger
  ) {
    this.scanStateRepo = new ScanStateRepository(pool, logger);
    this.blocksRepo = new BlocksRepository(pool, logger);
    this.depositsRepo = new DepositsRepository(pool, logger);
    this.seenTxsRepo = new SeenTxsRepository(pool, logger);
    this.addressesRepo = new AddressesRepository(pool, logger);
    this.parser = new TransactionParser(logger);
    this.reorgHandler = new ReorgHandler(pool, rpcClient, logger);
  }
  
  /**
   * Scan a single block
   */
  private async scanBlock(height: number, knownAddresses: Set<string>): Promise<void> {
    this.logger.debug({ height }, "Scanning block");
    
    // Fetch block
    const block = await this.rpcClient.getBlockByHeight(height);
    
    // Get scan state for parent verification
    const scanState = await this.scanStateRepo.get(this.config.assetNetworkId);
    
    if (!scanState) {
      throw new Error("Scan state not initialized");
    }
    
    // Verify parent linkage (reorg detection)
    if (height > 0 && scanState.cursor_hash && block.parent_hash !== scanState.cursor_hash) {
      this.logger.warn(
        { height, expectedParent: scanState.cursor_hash, actualParent: block.parent_hash },
        "Parent hash mismatch - reorg detected"
      );
      
      // Handle reorg
      await this.reorgHandler.handleReorg(
        this.config.assetNetworkId,
        height,
        scanState.cursor_hash,
        block.parent_hash,
        this.config.maxReorgDepth
      );
      
      // After reorg handling, cursor is rolled back
      // Return to allow next iteration to re-scan
      return;
    }
    
    // Fetch transactions for this block
    const txs = await this.fetchBlockTransactions(block);
    
    // Parse for deposits
    const deposits = this.parser.parseDeposits(txs, knownAddresses);
    
    // Process deposits in transaction
    await withTransaction(this.pool, async (client) => {
      for (const deposit of deposits) {
        const key = this.parser.createDepositKey(deposit.txid, deposit.vout);
        
        // Check if already seen (deduplication)
        const alreadySeen = await this.seenTxsRepo.hasSeen(key);
        if (alreadySeen) {
          this.logger.debug({ key }, "Transaction already seen, skipping");
          continue;
        }
        
        // Mark as seen
        await this.seenTxsRepo.markSeen(
          key,
          this.config.assetNetworkId,
          deposit.txid,
          height,
          deposit.address,
          deposit.amountAtoms,
          client
        );
        
        // Get user ID for this address
        const userId = await this.addressesRepo.getUserIdByAddress(
          this.config.assetNetworkId,
          deposit.address
        );
        
        // Create deposit record
        await this.depositsRepo.upsert(
          {
            userId,
            assetNetworkId: this.config.assetNetworkId,
            walletId: this.config.walletId,
            txid: deposit.txid,
            vout: deposit.vout,
            address: deposit.address,
            tag: null,
            amountAtoms: deposit.amountAtoms,
            confirmationsRequired: this.config.confirmationsRequired,
            blockHeight: height,
            blockHash: block.hash,
          },
          client
        );
        
        this.logger.info(
          {
            txid: deposit.txid,
            address: deposit.address,
            amount: deposit.amountAtoms,
            height,
          },
          "Deposit detected"
        );
      }
      
      // Store block
      await this.blocksRepo.upsert(
        this.config.assetNetworkId,
        height,
        block.hash,
        block.parent_hash,
        true,
        client
      );
      
      // Update cursor
      await this.scanStateRepo.updateCursor(
        this.config.assetNetworkId,
        height,
        block.hash,
        client
      );
    });
  }
  
  /**
   * Fetch all transactions for a block
   */
  private async fetchBlockTransactions(block: any): Promise<any[]> {
    const txs = [];
    
    for (const txid of block.txs) {
      try {
        const tx = await this.rpcClient.getTransaction(txid);
        tx.block_height = block.height;
        tx.block_hash = block.hash;
        txs.push(tx);
      } catch (error) {
        this.logger.warn({ txid, error }, "Failed to fetch transaction");
      }
    }
    
    return txs;
  }
  
  /**
   * Update confirmations for pending deposits
   */
  private async updateConfirmations(currentHeight: number): Promise<void> {
    // Get all detected/confirmed deposits
    const pendingDeposits = await this.depositsRepo.getByStatus(
      this.config.assetNetworkId,
      "DETECTED"
    );
    
    const confirmedDeposits = await this.depositsRepo.getByStatus(
      this.config.assetNetworkId,
      "CONFIRMED"
    );
    
    const allPending = [...pendingDeposits, ...confirmedDeposits];
    
    for (const deposit of allPending) {
      if (!deposit.block_height) continue;
      
      const confirmations = currentHeight - deposit.block_height + 1;
      
      // Update confirmations
      await this.depositsRepo.updateConfirmations(deposit.id, confirmations);
      
      // Transition to CONFIRMED if threshold met
      if (
        confirmations >= deposit.confirmations_required &&
        deposit.status === "DETECTED"
      ) {
        await this.depositsRepo.updateStatus(deposit.id, "CONFIRMED");
        this.logger.info(
          { depositId: deposit.id, confirmations },
          "Deposit confirmed"
        );
      }

      if (
        confirmations >= deposit.confirmations_required &&
        ["DETECTED", "CONFIRMED"].includes(deposit.status)
      ) {
        await this.depositsRepo.createCreditOutbox(deposit);
      }
    }
  }
  
  /**
   * Run one scan iteration
   */
  async scan(): Promise<number> {
    // Get chain head
    const head = await this.rpcClient.getHead();
    
    // Get scan state
    const scanState = await this.scanStateRepo.get(this.config.assetNetworkId);
    
    if (!scanState) {
      throw new Error("Scan state not initialized");
    }
    
    // Calculate safe target height (leave confirmations_required blocks unscanned)
    const safeHeight = head.height - (this.config.confirmationsRequired - 1);
    
    if (scanState.cursor_height >= safeHeight) {
      this.logger.debug(
        { cursor: scanState.cursor_height, safe: safeHeight },
        "Already at safe height"
      );
      
      // Update confirmations for pending deposits
      await this.updateConfirmations(head.height);
      
      return 0;
    }
    
    // Get known deposit addresses
    const knownAddresses = await this.addressesRepo.getActiveAddresses(
      this.config.assetNetworkId
    );
    
    // Scan in batches
    const fromHeight = scanState.cursor_height + 1;
    const toHeight = Math.min(fromHeight + this.config.scanBatch - 1, safeHeight);
    
    this.logger.info({ fromHeight, toHeight, headHeight: head.height }, "Scanning blocks");
    
    for (let height = fromHeight; height <= toHeight; height++) {
      await this.scanBlock(height, knownAddresses);
    }
    
    // Update confirmations for pending deposits
    await this.updateConfirmations(head.height);
    
    const scanned = toHeight - fromHeight + 1;
    return scanned;
  }
}
