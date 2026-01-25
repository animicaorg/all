/**
 * Poll Pending Withdrawals Job
 * 
 * Checks status of pending withdrawals and updates them
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { WithdrawalsRepository } from "../db/repositories/withdrawals_repo.js";
import { checkWithdrawalStatus } from "../withdrawals/tracker.js";
import { transact } from "../db/tx.js";

export class PollWithdrawalsJob {
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
      this.logger.warn("Poll withdrawals job already running");
      return;
    }

    this.logger.info(
      { intervalMs: this.config.WITHDRAWAL_POLL_INTERVAL_MS },
      "Starting poll withdrawals job"
    );

    this.intervalId = setInterval(() => {
      this.poll().catch((error) => {
        this.logger.error({ error }, "Error in poll withdrawals job");
      });
    }, this.config.WITHDRAWAL_POLL_INTERVAL_MS);

    // Run immediately
    this.poll().catch((error) => {
      this.logger.error({ error }, "Error in initial poll withdrawals run");
    });
  }

  /**
   * Stop the job
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      this.logger.info("Poll withdrawals job stopped");
    }
  }

  /**
   * Poll for pending withdrawals
   */
  private async poll(): Promise<void> {
    const withdrawalsRepo = new WithdrawalsRepository(this.pool);

    try {
      // Get pending withdrawals
      const pending = await withdrawalsRepo.getPendingForProvider(
        "ANIMICA_NODE",
        50
      );

      if (pending.length === 0) {
        this.logger.debug("No pending withdrawals to poll");
        return;
      }

      this.logger.info(
        { count: pending.length },
        "Polling pending withdrawals"
      );

      // Check each withdrawal
      for (const withdrawal of pending) {
        try {
          await this.checkWithdrawal(withdrawal.id, withdrawal.txid);
        } catch (error) {
          this.logger.error(
            { error, withdrawalId: withdrawal.id },
            "Error checking withdrawal"
          );
        }
      }
    } catch (error) {
      this.logger.error({ error }, "Error fetching pending withdrawals");
    }
  }

  /**
   * Check a single withdrawal
   */
  private async checkWithdrawal(
    withdrawalId: string,
    txid: string | null
  ): Promise<void> {
    if (!txid) {
      this.logger.debug(
        { withdrawalId },
        "Withdrawal has no txid yet, skipping status check"
      );
      return;
    }

    const status = await checkWithdrawalStatus(
      txid,
      this.config.ANIMICA_CONFIRMATIONS_REQUIRED,
      this.rpcClient,
      this.logger
    );

    const withdrawalsRepo = new WithdrawalsRepository(this.pool);

    // Update based on status
    if (status.status === "confirmed") {
      await transact(this.pool, this.logger, async (client) => {
        await withdrawalsRepo.updateStatus(
          withdrawalId,
          "CONFIRMED",
          {
            confirmed_at: new Date(),
          },
          client
        );

        this.logger.info(
          {
            withdrawalId,
            txid,
            confirmations: status.confirmations,
          },
          "Withdrawal confirmed"
        );
      });
    } else if (status.status === "failed") {
      await transact(this.pool, this.logger, async (client) => {
        await withdrawalsRepo.updateStatus(
          withdrawalId,
          "FAILED",
          {
            failure_code: "TX_FAILED",
            failure_message: status.error || "Transaction failed on chain",
          },
          client
        );

        this.logger.warn(
          { withdrawalId, txid, error: status.error },
          "Withdrawal failed"
        );
      });
    } else {
      this.logger.debug(
        {
          withdrawalId,
          txid,
          confirmations: status.confirmations,
          required: this.config.ANIMICA_CONFIRMATIONS_REQUIRED,
        },
        "Withdrawal still pending"
      );
    }
  }
}
