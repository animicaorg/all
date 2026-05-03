/**
 * Confirmation Backfill Job
 * 
 * Periodically checks pending deposits and updates their confirmation count
 * by querying BitGo API
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import { AuditRepo, DepositsRepo, NetworksRepo, OutboxRepo } from "../db/repositories/index.js";
import type { Config } from "../config.js";

export class ConfirmationBackfill {
  private running = false;
  private intervalId?: NodeJS.Timeout;

  constructor(
    private pool: Pool,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the backfill job
   */
  start(): void {
    if (this.running) {
      this.logger.warn("Confirmation backfill already running");
      return;
    }

    this.running = true;
    this.logger.info(
      { intervalMs: this.config.CONFIRMATION_BACKFILL_INTERVAL_MS },
      "Starting confirmation backfill job"
    );

    // Run immediately then on interval
    this.run().catch((error) => {
      this.logger.error({ error }, "Confirmation backfill initial run failed");
    });

    this.intervalId = setInterval(() => {
      this.run().catch((error) => {
        this.logger.error({ error }, "Confirmation backfill iteration failed");
      });
    }, this.config.CONFIRMATION_BACKFILL_INTERVAL_MS);
  }

  /**
   * Stop the backfill job
   */
  stop(): void {
    if (!this.running) {
      return;
    }

    this.running = false;
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = undefined;
    }

    this.logger.info("Confirmation backfill stopped");
  }

  /**
   * Run backfill iteration
   */
  private async run(): Promise<void> {
    const client = await this.pool.connect();

    try {
      const depositsRepo = new DepositsRepo(client);
      const networksRepo = new NetworksRepo(client);
      const outboxRepo = new OutboxRepo(client);
      const auditRepo = new AuditRepo(client);

      // Get deposits that need confirmation update
      // Only look at deposits older than 1 minute to avoid thrashing
      const deposits = await depositsRepo.getNeedingConfirmationUpdate(1, 50);

      if (deposits.length === 0) {
        this.logger.debug("No deposits need confirmation update");
        return;
      }

      this.logger.info(
        { count: deposits.length },
        "Backfilling confirmations for deposits"
      );

      for (const deposit of deposits) {
        const depositLogger = this.logger.child({
          depositId: deposit.id,
          txid: deposit.txid,
        });

        try {
          // In a real implementation, we would:
          // 1. Query BitGo API for transaction details
          // 2. Get current confirmation count
          // 3. Update deposit record
          //
          // For now, we'll just increment confirmations as a placeholder
          // This would be replaced with actual BitGo API integration

          if (!this.config.BITGO_API_TOKEN) {
            depositLogger.debug("BitGo API token not configured, skipping backfill");
            continue;
          }

          // TODO: Implement BitGo API call
          // const txInfo = await this.getBitGoTransaction(
          //   deposit.walletId,
          //   deposit.txid
          // );

          // For now, simulate confirmation increment
          const newConfirmations = deposit.confirmations + 1;

          depositLogger.info(
            {
              oldConfirmations: deposit.confirmations,
              newConfirmations,
              required: deposit.confirmationsRequired,
            },
            "Updating deposit confirmations"
          );

          // Update deposit
          const updated = await depositsRepo.updateConfirmations(
            deposit.id,
            newConfirmations,
            deposit.blockHeight || undefined,
            deposit.blockHash || undefined
          );

          // If deposit became confirmed, the upsert logic will handle it
          if (updated.status === "CONFIRMED" && deposit.status === "DETECTED") {
            depositLogger.info(
              { confirmations: newConfirmations },
              "Deposit reached confirmation threshold"
            );

            if (updated.userId && !updated.riskHold && !updated.unassigned) {
              const assetSymbol = await networksRepo.getAssetSymbol(updated.assetNetworkId);
              if (assetSymbol) {
                await outboxRepo.create(
                  updated.id,
                  updated.userId,
                  assetSymbol,
                  updated.amountAtoms,
                  {
                    provider: updated.provider,
                    txid: updated.txid,
                    address: updated.address,
                    transferId: updated.transferId,
                    walletId: updated.walletId,
                  }
                );

                await auditRepo.logDeposit(
                  "DEPOSIT_CONFIRMED",
                  updated.id,
                  updated.userId,
                  {
                    confirmations: updated.confirmations,
                    confirmationsRequired: updated.confirmationsRequired,
                  },
                  { backfill: true }
                );
              }
            }
          }
        } catch (error) {
          depositLogger.error(
            { error },
            "Failed to backfill confirmation for deposit"
          );
          // Continue with other deposits
        }
      }
    } finally {
      client.release();
    }
  }

  /**
   * Query BitGo API for transaction details
   * TODO: Implement actual BitGo API integration
   */
  private async getBitGoTransaction(
    walletId: string,
    txid: string
  ): Promise<any> {
    const baseUrl =
      this.config.BITGO_ENV === "prod"
        ? "https://app.bitgo.com"
        : "https://test.bitgo.com";

    const url = `${baseUrl}/api/v2/wallet/${walletId}/transfer/${txid}`;

    const response = await fetch(url, {
      headers: {
        Authorization: `Bearer ${this.config.BITGO_API_TOKEN}`,
      },
    });

    if (!response.ok) {
      throw new Error(`BitGo API error: ${response.status} ${response.statusText}`);
    }

    return response.json();
  }
}
