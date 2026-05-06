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
  mempoolScanEnabled?: boolean;
  mempoolMaxTxs?: number;
  balanceFallbackEnabled?: boolean;
}

export class BlockScanner {
  private scanStateRepo: ScanStateRepository;
  private blocksRepo: BlocksRepository;
  private depositsRepo: DepositsRepository;
  private seenTxsRepo: SeenTxsRepository;
  private addressesRepo: AddressesRepository;
  private parser: TransactionParser;
  private reorgHandler: ReorgHandler;
  private mempoolUnsupportedLogged = false;
  private balanceFallbackUnsupportedLogged = false;
  private historicalPolicyLogged = false;
  
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
  private async scanBlock(height: number, knownAddresses: Set<string>): Promise<boolean> {
    this.logger.debug({ height }, "Scanning block");
    
    // Fetch block
    const block = await this.rpcClient.getBlockByHeight(height);
    if (block.height !== height) {
      throw new Error(`RPC returned block ${block.height} while scanning height ${height}`);
    }
    
    const expectedParentHash = height > 0 ? await this.getExpectedParentHash(height - 1) : null;
    if (expectedParentHash && block.parent_hash !== expectedParentHash) {
      this.logger.warn(
        { height, expectedParent: expectedParentHash, actualParent: block.parent_hash },
        "Parent hash mismatch - reorg detected"
      );
      
      await this.reorgHandler.handleReorg(this.config.assetNetworkId, height - 1, this.config.maxReorgDepth);
      return false;
    }

    if (height > 0 && !expectedParentHash) {
      this.logger.info(
        { height, parentHeight: height - 1 },
        "No local parent anchor available; scanning forward without declaring a reorg"
      );
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
            status: "DETECTED",
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

    return true;
  }

  private async getExpectedParentHash(parentHeight: number): Promise<string | null> {
    const storedParent = await this.blocksRepo.getCanonicalByHeight(
      this.config.assetNetworkId,
      parentHeight
    );
    if (storedParent) return storedParent.hash;

    const scanState = await this.scanStateRepo.get(this.config.assetNetworkId);
    if (
      scanState?.cursor_hash &&
      scanState.cursor_height === parentHeight
    ) {
      return scanState.cursor_hash;
    }

    return null;
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

  private async validatePersistedCursor(headHeight: number): Promise<boolean> {
    const scanState = await this.scanStateRepo.get(this.config.assetNetworkId);
    if (!scanState?.cursor_hash) return true;

    if (scanState.cursor_height > headHeight) {
      this.logger.warn(
        { cursorHeight: scanState.cursor_height, headHeight },
        "Persisted cursor is above chain head - treating as possible reorg"
      );
      await this.reorgHandler.handleReorg(
        this.config.assetNetworkId,
        headHeight,
        this.config.maxReorgDepth
      );
      return false;
    }

    const cursorBlock = await this.rpcClient.getBlockByHeight(scanState.cursor_height);
    if (cursorBlock.hash !== scanState.cursor_hash) {
      this.logger.warn(
        {
          cursorHeight: scanState.cursor_height,
          storedHash: scanState.cursor_hash,
          chainHash: cursorBlock.hash,
        },
        "Persisted cursor hash no longer matches chain"
      );
      await this.reorgHandler.handleReorg(
        this.config.assetNetworkId,
        scanState.cursor_height,
        this.config.maxReorgDepth
      );
      return false;
    }

    return true;
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
      if (deposit.block_height === null || deposit.block_height === undefined) continue;
      
      const confirmations = Math.max(0, currentHeight - deposit.block_height + 1);
      
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

  private getNextHeight(scanState: { cursor_height: number; cursor_hash: string | null }): number {
    if (!Number.isSafeInteger(scanState.cursor_height) || scanState.cursor_height < 0) {
      throw new Error(`Invalid cursor height: ${String(scanState.cursor_height)}`);
    }
    return scanState.cursor_hash ? scanState.cursor_height + 1 : scanState.cursor_height;
  }

  private async scanMempool(knownAddresses: Set<string>): Promise<number> {
    if (this.config.mempoolScanEnabled === false || knownAddresses.size === 0) return 0;

    try {
      const pendingTxids = await this.rpcClient.getPendingTransactionIds();
      const maxTxs = Math.max(1, this.config.mempoolMaxTxs ?? 500);
      const txids = pendingTxids.slice(0, maxTxs);

      if (pendingTxids.length > maxTxs) {
        this.logger.warn(
          { pendingCount: pendingTxids.length, maxTxs },
          "Mempool pending list truncated for local address filtering"
        );
      }

      let detected = 0;
      for (const txid of txids) {
        try {
          const tx = await this.rpcClient.getMempoolTransaction(txid);
          const deposits = this.parser.parseDeposits([tx], knownAddresses);

          for (const deposit of deposits) {
            const userId = await this.addressesRepo.getUserIdByAddress(
              this.config.assetNetworkId,
              deposit.address
            );

            await this.depositsRepo.upsert({
              userId,
              assetNetworkId: this.config.assetNetworkId,
              walletId: this.config.walletId,
              txid: deposit.txid,
              vout: deposit.vout,
              address: deposit.address,
              tag: null,
              amountAtoms: deposit.amountAtoms,
              confirmationsRequired: this.config.confirmationsRequired,
              blockHeight: null,
              blockHash: null,
              status: "PENDING",
            });

            detected++;
            this.logger.info(
              { txid: deposit.txid, address: deposit.address, amount: deposit.amountAtoms },
              "Pending deposit detected in mempool"
            );
          }
        } catch (error) {
          this.logger.debug({ txid, error }, "Failed to inspect pending mempool transaction");
        }
      }

      return detected;
    } catch (error) {
      if (!this.mempoolUnsupportedLogged) {
        this.logger.warn(
          { error },
          "Mempool deposit pre-detection disabled; RPC does not expose a usable pending transaction list"
        );
        this.mempoolUnsupportedLogged = true;
      }
      return 0;
    }
  }

  private async reconcileConfirmedAddressBalances(
    knownAddresses: Set<string>,
    headHeight: number,
    headHash: string
  ): Promise<number> {
    if (this.config.balanceFallbackEnabled === false || knownAddresses.size === 0) return 0;

    let detected = 0;
    for (const address of knownAddresses) {
      let confirmedBalance: bigint;
      try {
        confirmedBalance = BigInt(await this.rpcClient.getConfirmedAddressBalance(address));
      } catch (error) {
        if (!this.balanceFallbackUnsupportedLogged) {
          this.logger.warn(
            { error },
            "Confirmed balance fallback disabled; RPC does not expose usable address balances"
          );
          this.balanceFallbackUnsupportedLogged = true;
        }
        return detected;
      }

      if (confirmedBalance <= 0n) continue;

      const accounted = await this.depositsRepo.getAccountedAmountByAddress(
        this.config.assetNetworkId,
        address
      );
      const delta = confirmedBalance - accounted;
      if (delta <= 0n) continue;

      const userId = await this.addressesRepo.getUserIdByAddress(
        this.config.assetNetworkId,
        address
      );
      if (!userId) continue;

      // Some confirmed Animica RPC transaction lookups expose only hash/block
      // metadata, which makes historical to/value recovery impossible. In that
      // case, credit the positive confirmed balance delta for an assigned
      // address as a synthetic, idempotent deposit and still enforce the normal
      // confirmation threshold from the detection height.
      const deposit = await this.depositsRepo.upsert({
        userId,
        assetNetworkId: this.config.assetNetworkId,
        walletId: this.config.walletId,
        txid: `balance:${address}:${headHeight}`,
        vout: "confirmed-balance",
        address,
        tag: null,
        amountAtoms: delta.toString(),
        confirmationsRequired: this.config.confirmationsRequired,
        blockHeight: headHeight,
        blockHash: headHash,
        status: "DETECTED",
      });

      const confirmations = Math.max(0, headHeight - (deposit.block_height ?? headHeight) + 1);
      await this.depositsRepo.updateConfirmations(deposit.id, confirmations);

      if (confirmations >= deposit.confirmations_required) {
        await this.depositsRepo.updateStatus(deposit.id, "CONFIRMED");
        await this.depositsRepo.createCreditOutbox({
          ...deposit,
          confirmations,
          status: "CONFIRMED",
        });
      }

      detected++;
      this.logger.info(
        {
          address,
          amount: delta.toString(),
          headHeight,
          accounted: accounted.toString(),
          confirmedBalance: confirmedBalance.toString(),
        },
        "Confirmed balance delta detected using address balance fallback"
      );
    }

    return detected;
  }
  
  /**
   * Run one scan iteration
   */
  async scan(): Promise<number> {
    // Get chain head
    const head = await this.rpcClient.getHead();
    const headHeight = Math.max(0, Number(head.height));
    if (!Number.isSafeInteger(headHeight)) {
      throw new Error(`Invalid chain head height: ${String(head.height)}`);
    }
    
    // Get scan state
    const scanState = await this.scanStateRepo.get(this.config.assetNetworkId);
    
    if (!scanState) {
      throw new Error("Scan state not initialized");
    }

    if (!(await this.validatePersistedCursor(headHeight))) {
      return 0;
    }

    const knownAddresses = await this.addressesRepo.getActiveAddresses(
      this.config.assetNetworkId
    );

    if (!this.historicalPolicyLogged) {
      this.logger.info(
        { assetNetworkId: this.config.assetNetworkId },
        "Historical scan policy: credit all on-chain deposits to currently assigned active addresses"
      );
      this.historicalPolicyLogged = true;
    }

    const fromHeight = this.getNextHeight(scanState);
    const toHeight = Math.min(
      fromHeight + Math.max(1, this.config.scanBatch) - 1,
      headHeight
    );
    
    if (fromHeight > headHeight) {
      this.logger.debug(
        { cursor: scanState.cursor_height, headHeight },
        "Already at chain head"
      );
      
      // Update confirmations for pending deposits
      await this.updateConfirmations(headHeight);
      await this.scanMempool(knownAddresses);
      await this.reconcileConfirmedAddressBalances(knownAddresses, headHeight, head.hash);
      
      return 0;
    }

    this.logger.info({ fromHeight, toHeight, headHeight }, "Scanning blocks");
    
    let scanned = 0;
    for (let height = fromHeight; height <= toHeight; height++) {
      const completed = await this.scanBlock(height, knownAddresses);
      if (!completed) break;
      scanned++;
    }
    
    // Update confirmations for pending deposits
    await this.updateConfirmations(headHeight);
    await this.scanMempool(knownAddresses);
    await this.reconcileConfirmedAddressBalances(knownAddresses, headHeight, head.hash);
    
    return scanned;
  }
}
