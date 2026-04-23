/**
 * Outbox Worker
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";
import type { BitGoClient } from "../bitgo/client.js";
import axios from "axios";
import {
  getPendingOperations,
  markProcessing,
  markCompleted,
  markFailed,
  markPermanentlyFailed,
  type OutboxOperation,
} from "./outbox.js";
import { submitToBitGo } from "../pipeline/submit.js";
import { calculateBackoff } from "../pipeline/retries.js";

export class OutboxWorker {
  private intervalId: NodeJS.Timeout | null = null;
  private processing = false;

  constructor(
    private pool: Pool,
    private bitgoClient: BitGoClient,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the outbox worker
   */
  start(): void {
    if (this.intervalId) {
      this.logger.warn("Outbox worker already running");
      return;
    }

    this.logger.info(
      { intervalMs: this.config.OUTBOX_WORKER_INTERVAL_MS },
      "Starting outbox worker"
    );

    this.intervalId = setInterval(() => {
      this.processOperations().catch((error) => {
        this.logger.error({ err: error }, "Error in outbox worker");
      });
    }, this.config.OUTBOX_WORKER_INTERVAL_MS);

    // Run immediately
    this.processOperations().catch((error) => {
      this.logger.error({ err: error }, "Error in initial outbox worker run");
    });
  }

  /**
   * Stop the outbox worker
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      this.logger.info("Outbox worker stopped");
    }
  }

  /**
   * Process pending outbox operations
   */
  private async processOperations(): Promise<void> {
    if (this.processing) {
      this.logger.debug("Outbox worker already processing");
      return;
    }

    this.processing = true;

    try {
      const client = await this.pool.connect();

      try {
        await client.query("BEGIN");

        // Get pending operations with lock
        const operations = await getPendingOperations(client, 10);

        if (operations.length === 0) {
          await client.query("COMMIT");
          return;
        }

        this.logger.info(
          { operationCount: operations.length },
          "Processing outbox operations"
        );

        // Process each operation
        for (const operation of operations) {
          await this.processOperation(client, operation);
        }

        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK");
        throw error;
      } finally {
        client.release();
      }
    } finally {
      this.processing = false;
    }
  }

  /**
   * Process a single outbox operation
   */
  private async processOperation(
    client: any,
    operation: OutboxOperation
  ): Promise<void> {
    try {
      this.logger.debug(
        {
          operationId: operation.id,
          type: operation.type,
          withdrawalId: operation.withdrawalId,
          attemptCount: operation.attemptCount,
        },
        "Processing outbox operation"
      );

      await markProcessing(client, operation.id);

      let result: { success: boolean; message: string };

      switch (operation.type) {
        case "APPLY_LEDGER_LOCK":
          result = await this.applyLedgerLock(operation.payload);
          break;

        case "SUBMIT_TO_BITGO":
          result = await submitToBitGo(
            client,
            operation.withdrawalId,
            this.bitgoClient,
            this.logger
          );
          break;

        case "APPLY_LEDGER_BROADCAST":
          result = await this.applyLedgerBroadcast(operation.payload);
          break;

        case "APPLY_LEDGER_CANCEL":
          result = await this.applyLedgerCancel(operation.payload);
          break;

        default:
          throw new Error(`Unknown operation type: ${operation.type}`);
      }

      if (result.success) {
        await markCompleted(client, operation.id);
        this.logger.info(
          {
            operationId: operation.id,
            type: operation.type,
            message: result.message,
          },
          "Outbox operation completed"
        );
      } else {
        throw new Error(result.message);
      }
    } catch (error: any) {
      this.logger.error(
        {
          err: error,
          operationId: operation.id,
          type: operation.type,
          attemptCount: operation.attemptCount,
        },
        "Outbox operation failed"
      );

      // Check if we should retry
      if (operation.attemptCount >= 9) {
        // Max attempts reached
        await markPermanentlyFailed(client, operation.id, {
          message: error.message,
          stack: error.stack,
        });
      } else {
        // Schedule retry with exponential backoff
        const retryDelayMs = calculateBackoff(operation.attemptCount);
        await markFailed(client, operation.id, {
          message: error.message,
          stack: error.stack,
        }, retryDelayMs);
      }
    }
  }

  /**
   * Apply ledger lock (deduct from available balance)
   */
  private async applyLedgerLock(payload: any): Promise<{ success: boolean; message: string }> {
    try {
      const response = await axios.post(
        `${this.config.LEDGER_SERVICE_URL}/internal/lock`,
        {
          userId: payload.userId,
          assetNetworkId: payload.assetNetworkId,
          amount: payload.amount,
          reason: "WITHDRAWAL",
          referenceId: payload.withdrawalId,
        },
        {
          timeout: 10000,
        }
      );

      this.logger.info(
        {
          withdrawalId: payload.withdrawalId,
          ledgerTxId: response.data.transactionId,
        },
        "Ledger lock applied"
      );

      return { success: true, message: "Ledger lock applied" };
    } catch (error: any) {
      this.logger.error(
        { err: error, payload },
        "Failed to apply ledger lock"
      );
      throw error;
    }
  }

  /**
   * Apply ledger broadcast (move from available to system)
   */
  private async applyLedgerBroadcast(payload: any): Promise<{ success: boolean; message: string }> {
    try {
      const response = await axios.post(
        `${this.config.LEDGER_SERVICE_URL}/internal/broadcast`,
        {
          userId: payload.userId,
          withdrawalId: payload.withdrawalId,
          txid: payload.txid,
        },
        {
          timeout: 10000,
        }
      );

      this.logger.info(
        {
          withdrawalId: payload.withdrawalId,
          ledgerTxId: response.data.transactionId,
        },
        "Ledger broadcast applied"
      );

      return { success: true, message: "Ledger broadcast applied" };
    } catch (error: any) {
      this.logger.error(
        { err: error, payload },
        "Failed to apply ledger broadcast"
      );
      throw error;
    }
  }

  /**
   * Apply ledger cancel (release locked funds)
   */
  private async applyLedgerCancel(payload: any): Promise<{ success: boolean; message: string }> {
    try {
      const response = await axios.post(
        `${this.config.LEDGER_SERVICE_URL}/internal/cancel`,
        {
          userId: payload.userId,
          withdrawalId: payload.withdrawalId,
          reason: payload.reason,
        },
        {
          timeout: 10000,
        }
      );

      this.logger.info(
        {
          withdrawalId: payload.withdrawalId,
          ledgerTxId: response.data.transactionId,
        },
        "Ledger cancel applied"
      );

      return { success: true, message: "Ledger cancel applied" };
    } catch (error: any) {
      this.logger.error(
        { err: error, payload },
        "Failed to apply ledger cancel"
      );
      throw error;
    }
  }
}
