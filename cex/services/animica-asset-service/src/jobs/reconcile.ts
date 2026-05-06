/**
 * Reconciliation Job
 * 
 * Reconciles internal state with blockchain state
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { DepositsRepository } from "../db/repositories/deposits_repo.js";
import { WithdrawalsRepository } from "../db/repositories/withdrawals_repo.js";

export class ReconciliationJob {
  private intervalId: NodeJS.Timeout | null = null;

  constructor(
    private pool: Pool,
    private rpcClient: AnimicaRpcClient,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the job
   */
  start(): void {
    if (this.intervalId) {
      this.logger.warn("Reconciliation job already running");
      return;
    }

    this.logger.info(
      { intervalMs: this.config.RECONCILE_INTERVAL_MS },
      "Starting reconciliation job"
    );

    this.intervalId = setInterval(() => {
      this.reconcile().catch((error) => {
        this.logger.error({ error }, "Error in reconciliation job");
      });
    }, this.config.RECONCILE_INTERVAL_MS);

    // Run after a delay to avoid startup congestion
    setTimeout(() => {
      this.reconcile().catch((error) => {
        this.logger.error({ error }, "Error in initial reconciliation run");
      });
    }, 30000); // 30 seconds
  }

  /**
   * Stop the job
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      this.logger.info("Reconciliation job stopped");
    }
  }

  /**
   * Run reconciliation
   */
  private async reconcile(): Promise<void> {
    this.logger.info("Starting reconciliation");

    const depositsRepo = new DepositsRepository(this.pool, this.logger);
    const withdrawalsRepo = new WithdrawalsRepository(this.pool);

    try {
      // Check node health
      const healthy = await this.rpcClient.health();
      if (!healthy) {
        this.logger.warn("Node unhealthy, skipping reconciliation");
        return;
      }

      // Get summary stats
      const depositStats = await this.getDepositStats(depositsRepo);
      const withdrawalStats = await this.getWithdrawalStats(withdrawalsRepo);

      this.logger.info(
        {
          deposits: depositStats,
          withdrawals: withdrawalStats,
        },
        "Reconciliation summary"
      );

      // Log any anomalies
      if (depositStats.unconfirmedCount > 100) {
        this.logger.warn(
          { count: depositStats.unconfirmedCount },
          "High number of unconfirmed deposits"
        );
      }

      if (withdrawalStats.broadcastCount > 50) {
        this.logger.warn(
          { count: withdrawalStats.broadcastCount },
          "High number of broadcast but unconfirmed withdrawals"
        );
      }

      if (withdrawalStats.signingCount > 20) {
        this.logger.warn(
          { count: withdrawalStats.signingCount },
          "High number of withdrawals stuck in signing"
        );
      }

      this.logger.info("Reconciliation complete");
    } catch (error) {
      this.logger.error({ error }, "Reconciliation failed");
    }
  }

  /**
   * Get deposit statistics
   */
  private async getDepositStats(
    depositsRepo: DepositsRepository
  ): Promise<{
    total: number;
    confirmedCount: number;
    unconfirmedCount: number;
  }> {
    const query = `
      SELECT 
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE confirmations >= $1) as confirmed_count,
        COUNT(*) FILTER (WHERE confirmations < $1) as unconfirmed_count
      FROM deposits
      WHERE asset_network_id = $2
    `;

    const result = await this.pool.query(query, [
      this.config.ANIMICA_CONFIRMATIONS_REQUIRED,
      this.config.ANIMICA_ASSET_NETWORK_ID,
    ]);

    const row = result.rows[0];
    return {
      total: parseInt(row.total),
      confirmedCount: parseInt(row.confirmed_count),
      unconfirmedCount: parseInt(row.unconfirmed_count),
    };
  }

  /**
   * Get withdrawal statistics
   */
  private async getWithdrawalStats(
    withdrawalsRepo: WithdrawalsRepository
  ): Promise<{
    total: number;
    confirmedCount: number;
    broadcastCount: number;
    signingCount: number;
    failedCount: number;
  }> {
    const query = `
      SELECT 
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE status = 'CONFIRMED') as confirmed_count,
        COUNT(*) FILTER (WHERE status = 'BROADCAST') as broadcast_count,
        COUNT(*) FILTER (WHERE status = 'SIGNING') as signing_count,
        COUNT(*) FILTER (WHERE status = 'FAILED') as failed_count
      FROM withdrawals
      WHERE provider = 'ANIMICA_NODE'
        AND asset_network_id = $1
    `;

    const result = await this.pool.query(query, [
      this.config.ANIMICA_ASSET_NETWORK_ID,
    ]);

    const row = result.rows[0];
    return {
      total: parseInt(row.total),
      confirmedCount: parseInt(row.confirmed_count),
      broadcastCount: parseInt(row.broadcast_count),
      signingCount: parseInt(row.signing_count),
      failedCount: parseInt(row.failed_count),
    };
  }
}
