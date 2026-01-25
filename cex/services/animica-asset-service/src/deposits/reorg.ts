/**
 * Reorg Handler
 * 
 * Handles blockchain reorganizations by:
 * 1. Detecting parent hash mismatches
 * 2. Rolling back to common ancestor
 * 3. Marking affected deposits as reorged
 * 4. Creating audit alerts
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { ScanStateRepository } from "../db/repositories/scan_state_repo.js";
import { BlocksRepository } from "../db/repositories/blocks_repo.js";
import { DepositsRepository } from "../db/repositories/deposits_repo.js";
import { SeenTxsRepository } from "../db/repositories/seen_txs_repo.js";
import { withTransaction } from "../db/tx.js";

export interface ReorgResult {
  commonAncestorHeight: number;
  commonAncestorHash: string;
  reorgedBlocks: number;
  affectedDeposits: number;
}

export class ReorgHandler {
  private scanStateRepo: ScanStateRepository;
  private blocksRepo: BlocksRepository;
  private depositsRepo: DepositsRepository;
  private seenTxsRepo: SeenTxsRepository;
  
  constructor(
    private pool: Pool,
    private rpcClient: AnimicaRpcClient,
    private logger: Logger
  ) {
    this.scanStateRepo = new ScanStateRepository(pool, logger);
    this.blocksRepo = new BlocksRepository(pool, logger);
    this.depositsRepo = new DepositsRepository(pool, logger);
    this.seenTxsRepo = new SeenTxsRepository(pool, logger);
  }
  
  /**
   * Handle reorg by rolling back to common ancestor
   */
  async handleReorg(
    assetNetworkId: string,
    currentHeight: number,
    expectedParentHash: string,
    actualParentHash: string,
    maxReorgDepth: number
  ): Promise<ReorgResult> {
    this.logger.warn(
      { currentHeight, expectedParentHash, actualParentHash },
      "Reorg detected - finding common ancestor"
    );
    
    let searchHeight = currentHeight - 1;
    let commonAncestorHeight = searchHeight;
    let commonAncestorHash = expectedParentHash;
    let steps = 0;
    
    // Walk backwards to find common ancestor
    while (steps < maxReorgDepth) {
      // Get our stored block at this height
      const storedBlock = await this.blocksRepo.getByHeight(assetNetworkId, searchHeight);
      
      if (!storedBlock) {
        // No stored block - assume this is safe to use
        this.logger.info(
          { height: searchHeight },
          "No stored block at height, assuming common ancestor"
        );
        commonAncestorHeight = searchHeight;
        break;
      }
      
      // Get current chain's block at this height
      const chainBlock = await this.rpcClient.getBlockByHeight(searchHeight);
      
      if (storedBlock.hash === chainBlock.hash) {
        // Found common ancestor
        commonAncestorHeight = searchHeight;
        commonAncestorHash = storedBlock.hash;
        this.logger.info(
          { height: commonAncestorHeight, hash: commonAncestorHash },
          "Common ancestor found"
        );
        break;
      }
      
      searchHeight--;
      steps++;
    }
    
    if (steps >= maxReorgDepth) {
      throw new Error(
        `Reorg exceeds max depth ${maxReorgDepth}. Manual intervention required.`
      );
    }
    
    // Execute rollback in transaction
    const result = await withTransaction(this.pool, async (client) => {
      const reorgedFromHeight = commonAncestorHeight + 1;
      const reorgedToHeight = currentHeight;
      const reorgedBlocks = reorgedToHeight - reorgedFromHeight + 1;
      
      // Mark blocks as non-canonical
      await this.blocksRepo.markNonCanonical(
        assetNetworkId,
        reorgedFromHeight,
        reorgedToHeight,
        client
      );
      
      // Get affected deposits
      const affectedDeposits = await this.depositsRepo.getByHeightRange(
        assetNetworkId,
        reorgedFromHeight,
        reorgedToHeight
      );
      
      // Mark deposits as reorged
      const depositIds = affectedDeposits.map((d) => d.id);
      await this.depositsRepo.markReorged(depositIds, client);
      
      // Delete seen transactions
      await this.seenTxsRepo.deleteByHeightRange(
        assetNetworkId,
        reorgedFromHeight,
        reorgedToHeight,
        client
      );
      
      // Rollback scan cursor
      await this.scanStateRepo.rollbackCursor(
        assetNetworkId,
        commonAncestorHeight,
        commonAncestorHash,
        client
      );
      
      // Create audit log for credited deposits
      const creditedDeposits = affectedDeposits.filter((d) => d.status === "CREDITED");
      if (creditedDeposits.length > 0) {
        this.logger.error(
          {
            count: creditedDeposits.length,
            depositIds: creditedDeposits.map((d) => d.id),
          },
          "CRITICAL: Credited deposits affected by reorg - manual review required"
        );
        
        // Insert audit alert
        const auditQuery = `
          INSERT INTO audit_logs (
            event_type, resource_type, resource_id, actor_type, changes, metadata
          )
          VALUES ($1, $2, $3, $4, $5, $6)
        `;
        
        for (const deposit of creditedDeposits) {
          await client.query(auditQuery, [
            "DEPOSIT_REORGED_CREDITED",
            "DEPOSIT",
            deposit.id,
            "SYSTEM",
            JSON.stringify({ reorg: { commonAncestorHeight, reorgedBlocks } }),
            JSON.stringify({ txid: deposit.txid, amount: deposit.amount_atoms }),
          ]);
        }
      }
      
      return {
        commonAncestorHeight,
        commonAncestorHash,
        reorgedBlocks,
        affectedDeposits: affectedDeposits.length,
      };
    });
    
    this.logger.warn(result, "Reorg handling complete");
    return result;
  }
}
