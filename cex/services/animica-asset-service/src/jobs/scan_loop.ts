/**
 * Scan Loop Job with Leader Election
 * 
 * Scans blockchain for deposits with automatic leader election
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { ScanStateRepository } from "../db/repositories/scan_state_repo.js";
import { BlockScanner, type ScannerConfig } from "../deposits/scanner.js";

export class ScanLoopJob {
  private intervalId: NodeJS.Timeout | null = null;
  private isLeader: boolean = false;
  private tickInProgress: boolean = false;
  private scanner: BlockScanner;
  private scanStateRepo: ScanStateRepository;

  constructor(
    private pool: Pool,
    private rpcClient: AnimicaRpcClient,
    private config: Config,
    private logger: Logger
  ) {
    this.scanStateRepo = new ScanStateRepository(pool, logger);
    
    // Create scanner config from main config
    const scannerConfig = {
      assetNetworkId: config.ANIMICA_ASSET_NETWORK_ID,
      confirmationsRequired: config.ANIMICA_CONFIRMATIONS_REQUIRED,
      scanBatch: config.ANIMICA_SCAN_BATCH,
      maxReorgDepth: config.ANIMICA_MAX_REORG_DEPTH,
      walletId: "ANIMICA_NODE", // provider identifier
      mempoolScanEnabled: config.ANIMICA_MEMPOOL_SCAN_ENABLED,
      mempoolMaxTxs: config.ANIMICA_MEMPOOL_MAX_TXS,
      balanceFallbackEnabled: config.ANIMICA_BALANCE_FALLBACK_ENABLED,
    };
    
    this.scanner = new BlockScanner(pool, rpcClient, scannerConfig, logger);
  }

  /**
   * Start the scan loop
   */
  start(): void {
    if (this.intervalId) {
      this.logger.warn("Scan loop already running");
      return;
    }

    this.logger.info(
      {
        intervalMs: this.config.SCAN_WORKER_INTERVAL_MS,
        instanceId: this.config.INSTANCE_ID,
      },
      "Starting scan loop job"
    );

    // Initialize scan state
    this.initializeScanState().catch((error) => {
      this.logger.error({ error }, "Failed to initialize scan state");
    });

    // Run periodically
    this.intervalId = setInterval(() => {
      this.tick().catch((error) => {
        this.logger.error({ error }, "Error in scan loop tick");
      });
    }, this.config.SCAN_WORKER_INTERVAL_MS);

    // Run immediately
    this.tick().catch((error) => {
      this.logger.error({ error }, "Error in initial scan loop tick");
    });
  }

  /**
   * Stop the scan loop
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;

      // Release lock if we're the leader
      if (this.isLeader) {
        this.releaseLock().catch((error) => {
          this.logger.error({ error }, "Failed to release lock on stop");
        });
      }

      this.logger.info("Scan loop job stopped");
    }
  }

  /**
   * Initialize scan state in database
   */
  private async initializeScanState(): Promise<void> {
    try {
      await this.scanStateRepo.initialize(
        this.config.ANIMICA_ASSET_NETWORK_ID,
        this.config.ANIMICA_SCAN_START_HEIGHT
      );
    } catch (error) {
      // Ignore if already initialized
      this.logger.debug({ error }, "Scan state initialization skipped (likely already exists)");
    }
  }

  /**
   * Single tick of the scan loop
   */
  private async tick(): Promise<void> {
    if (this.tickInProgress) {
      this.logger.debug("Previous scan tick still running, skipping overlapping tick");
      return;
    }

    this.tickInProgress = true;

    // Try to acquire lock (leader election)
    try {
      const acquired = await this.tryAcquireLock();

      if (!acquired) {
        this.logger.debug("Not the leader, skipping scan");
        return;
      }

      this.isLeader = true;

      // Run scanner
      await this.scanner.scan();

      // Renew lock to keep leadership
      await this.renewLock();
    } catch (error) {
      this.logger.error({ error }, "Error during scan");
      // Don't release lock - let it expire naturally
    } finally {
      this.tickInProgress = false;
    }
  }

  /**
   * Try to acquire scan lock
   */
  private async tryAcquireLock(): Promise<boolean> {
    try {
      return await this.scanStateRepo.acquireLock(
        this.config.ANIMICA_ASSET_NETWORK_ID,
        this.config.INSTANCE_ID,
        this.config.SCAN_LOCK_TTL_MS
      );
    } catch (error) {
      this.logger.error({ error }, "Failed to acquire lock");
      return false;
    }
  }

  /**
   * Renew scan lock
   */
  private async renewLock(): Promise<void> {
    try {
      const renewed = await this.scanStateRepo.renewLock(
        this.config.ANIMICA_ASSET_NETWORK_ID,
        this.config.INSTANCE_ID,
        this.config.SCAN_LOCK_TTL_MS
      );

      if (!renewed) {
        this.logger.warn("Failed to renew lock, lost leadership");
        this.isLeader = false;
      }
    } catch (error) {
      this.logger.error({ error }, "Error renewing lock");
      this.isLeader = false;
    }
  }

  /**
   * Release scan lock
   */
  private async releaseLock(): Promise<void> {
    try {
      await this.scanStateRepo.releaseLock(
        this.config.ANIMICA_ASSET_NETWORK_ID,
        this.config.INSTANCE_ID
      );
      this.isLeader = false;
      this.logger.info("Released scan lock");
    } catch (error) {
      this.logger.error({ error }, "Failed to release lock");
    }
  }
}
