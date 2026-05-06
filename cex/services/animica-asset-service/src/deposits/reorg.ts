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
    firstMismatchedHeight: number,
    maxReorgDepth: number
  ): Promise<ReorgResult> {
    const scanState = await this.scanStateRepo.get(assetNetworkId);
    if (!scanState || !scanState.cursor_hash) {
      throw new Error("Cannot handle reorg without a persisted scan cursor");
    }

    const cursorHeight = scanState.cursor_height;
    const searchStartHeight = Math.min(firstMismatchedHeight, cursorHeight);
    const minSearchHeight = Math.max(0, searchStartHeight - maxReorgDepth);

    this.logger.warn(
      { firstMismatchedHeight, cursorHeight, minSearchHeight },
      "Reorg detected - finding common ancestor"
    );
    
    let commonAncestorHeight: number | null = null;
    let commonAncestorHash: string | null = null;
    
    // Walk backwards to find common ancestor
    for (let searchHeight = searchStartHeight; searchHeight >= minSearchHeight; searchHeight--) {
      const chainBlock = await this.rpcClient.getBlockByHeight(searchHeight);
      const storedBlock = await this.blocksRepo.getCanonicalByHeight(assetNetworkId, searchHeight);

      if (storedBlock && storedBlock.hash === chainBlock.hash) {
        commonAncestorHeight = searchHeight;
        commonAncestorHash = storedBlock.hash;
        break;
      }

      if (
        !storedBlock &&
        scanState.cursor_height === searchHeight &&
        scanState.cursor_hash === chainBlock.hash
      ) {
        commonAncestorHeight = searchHeight;
        commonAncestorHash = chainBlock.hash;
        break;
      }

      if (!storedBlock) {
        this.logger.debug(
          { height: searchHeight, chainHash: chainBlock.hash },
          "No stored canonical block at height while searching for common ancestor"
        );
      }

      if (searchHeight === 0) break;
    }
    
    if (commonAncestorHeight === null || commonAncestorHash === null) {
      throw new Error(
        `Reorg common ancestor not found within max depth ${maxReorgDepth}. Manual intervention required.`
      );
    }

    this.logger.info(
      { height: commonAncestorHeight, hash: commonAncestorHash },
      "Common ancestor found"
    );

    const commonHeight = commonAncestorHeight;
    const commonHash = commonAncestorHash;
    
    // Execute rollback in transaction
    const result = await withTransaction(this.pool, async (client) => {
      const reorgedFromHeight = commonHeight + 1;
      const reorgedToHeight = cursorHeight;
      const reorgedBlocks =
        reorgedFromHeight <= reorgedToHeight ? reorgedToHeight - reorgedFromHeight + 1 : 0;
      
      const affectedDeposits =
        reorgedBlocks > 0
          ? await this.depositsRepo.getByHeightRange(
              assetNetworkId,
              reorgedFromHeight,
              reorgedToHeight
            )
          : [];

      if (reorgedBlocks > 0) {
        // Mark blocks as non-canonical only after a real common ancestor is proven.
        await this.blocksRepo.markNonCanonical(
          assetNetworkId,
          reorgedFromHeight,
          reorgedToHeight,
          client
        );

        const depositIds = affectedDeposits.map((d) => d.id);
        await this.depositsRepo.markReorged(depositIds, client);

        await this.seenTxsRepo.deleteByHeightRange(
          assetNetworkId,
          reorgedFromHeight,
          reorgedToHeight,
          client
        );
      }
      
      // Rollback scan cursor
      await this.scanStateRepo.rollbackCursor(
        assetNetworkId,
        commonHeight,
        commonHash,
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
            event_type, resource_type, resource_id, actor_type,
            action, entity_type, entity_id, changes, metadata
          )
          VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        `;
        
        for (const deposit of creditedDeposits) {
          await client.query(auditQuery, [
            "DEPOSIT_REORGED_CREDITED",
            "DEPOSIT",
            deposit.id,
            "SYSTEM",
            "DEPOSIT_REORGED_CREDITED",
            "DEPOSIT",
            deposit.id,
            JSON.stringify({ reorg: { commonAncestorHeight: commonHeight, reorgedBlocks } }),
            JSON.stringify({ txid: deposit.txid, amount: deposit.amount_atoms }),
          ]);
        }
      }
      
      return {
        commonAncestorHeight: commonHeight,
        commonAncestorHash: commonHash,
        reorgedBlocks,
        affectedDeposits: affectedDeposits.length,
      };
    });
    
    this.logger.warn(result, "Reorg handling complete");
    return result;
  }
}
