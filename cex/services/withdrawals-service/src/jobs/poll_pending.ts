/**
 * Poll Pending Withdrawals Job
 * 
 * Queries BitGo for status of withdrawals stuck in SIGNING/BROADCAST states
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { BitGoClient } from "../bitgo/client.js";
import { WithdrawalsRepo, NetworksRepo } from "../db/repositories/index.js";
import { processWebhook } from "../pipeline/tracker.js";
import type { WithdrawalObservation } from "../bitgo/types.js";

export class PollPendingJob {
  private intervalId: NodeJS.Timeout | null = null;

  constructor(
    private pool: Pool,
    private bitgoClient: BitGoClient,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the job
   */
  start(): void {
    if (this.intervalId) {
      this.logger.warn("Poll pending job already running");
      return;
    }

    this.logger.info(
      { intervalMs: this.config.POLL_PENDING_INTERVAL_MS },
      "Starting poll pending job"
    );

    this.intervalId = setInterval(() => {
      this.poll().catch((error) => {
        this.logger.error({ error }, "Error in poll pending job");
      });
    }, this.config.POLL_PENDING_INTERVAL_MS);

    // Run immediately
    this.poll().catch((error) => {
      this.logger.error({ error }, "Error in initial poll pending job run");
    });
  }

  /**
   * Stop the job
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      this.logger.info("Poll pending job stopped");
    }
  }

  /**
   * Poll for pending withdrawals
   */
  private async poll(): Promise<void> {
    const client = await this.pool.connect();

    try {
      const withdrawalsRepo = new WithdrawalsRepo(client);
      const networksRepo = new NetworksRepo(client);

      // Get withdrawals that need status update
      const pendingWithdrawals = await withdrawalsRepo.getPendingForRetry(50);

      if (pendingWithdrawals.length === 0) {
        return;
      }

      this.logger.info(
        { count: pendingWithdrawals.length },
        "Polling pending withdrawals"
      );

      for (const withdrawal of pendingWithdrawals) {
        try {
          if (!withdrawal.providerRef) {
            this.logger.warn(
              { withdrawalId: withdrawal.id },
              "Withdrawal has no provider reference, skipping"
            );
            continue;
          }

          // Get network and wallet
          const [assetNetwork, wallet] = await Promise.all([
            networksRepo.getAssetNetwork(withdrawal.assetNetworkId),
            networksRepo.getWallet(withdrawal.assetNetworkId, "HOT"),
          ]);

          if (!assetNetwork?.bitgoCoin) {
            this.logger.warn(
              { withdrawalId: withdrawal.id, assetNetworkId: withdrawal.assetNetworkId },
              "No BitGo coin configured for withdrawal"
            );
            continue;
          }

          if (!wallet) {
            this.logger.warn(
              { withdrawalId: withdrawal.id },
              "No wallet found for withdrawal"
            );
            continue;
          }

          // Query BitGo for transfer status
          const response = await this.bitgoClient.getTransfer(
            assetNetwork.bitgoCoin,
            wallet.providerWalletId,
            withdrawal.providerRef
          );

          // Map BitGo state to our state
          let state: WithdrawalObservation["state"];
          switch (response.transfer.state) {
            case "pending":
            case "pendingApproval":
              state = "SIGNING";
              break;
            case "signed":
              state = "BROADCAST";
              break;
            case "confirmed":
              state = "CONFIRMED";
              break;
            case "failed":
            case "rejected":
            case "removed":
              state = "FAILED";
              break;
            default:
              state = "SIGNING";
          }

          // Create observation
          const observation: WithdrawalObservation = {
            provider: "BITGO",
            providerRef: withdrawal.providerRef,
            walletId: wallet.providerWalletId,
            txid: response.transfer.txid || null,
            state,
            amountAtoms: BigInt(response.transfer.value),
            observedAt: new Date(),
            raw: response,
          };

          // Process as webhook
          await client.query("BEGIN");
          await processWebhook(client, observation, this.logger);
          await client.query("COMMIT");

          this.logger.info(
            {
              withdrawalId: withdrawal.id,
              providerRef: withdrawal.providerRef,
              state,
            },
            "Polled withdrawal status"
          );
        } catch (error) {
          this.logger.error(
            {
              error,
              withdrawalId: withdrawal.id,
            },
            "Failed to poll withdrawal status"
          );
          
          // Rollback transaction if started
          try {
            await client.query("ROLLBACK");
          } catch {
            // Ignore
          }
        }
      }
    } finally {
      client.release();
    }
  }
}
