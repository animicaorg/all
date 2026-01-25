/**
 * Outbox Processor Job
 * 
 * Processes pending outbox items to send deposit credits to ledger service
 */

import type { Pool } from "pg";
import type { NatsConnection } from "nats";
import type { Logger } from "pino";
import { OutboxRepo, DepositsRepo, AuditRepo } from "../db/repositories/index.js";
import { sendDepositCredit, sendDepositCreditHttp } from "../pipeline/credit.js";
import type { Config } from "../config.js";

export class OutboxProcessor {
  private running = false;
  private intervalId?: NodeJS.Timeout;

  constructor(
    private pool: Pool,
    private nats: NatsConnection | null,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the processor
   */
  start(): void {
    if (this.running) {
      this.logger.warn("Outbox processor already running");
      return;
    }

    this.running = true;
    this.logger.info(
      { intervalMs: this.config.OUTBOX_PROCESSOR_INTERVAL_MS },
      "Starting outbox processor"
    );

    // Process immediately then on interval
    this.process().catch((error) => {
      this.logger.error({ error }, "Outbox processor initial run failed");
    });

    this.intervalId = setInterval(() => {
      this.process().catch((error) => {
        this.logger.error({ error }, "Outbox processor iteration failed");
      });
    }, this.config.OUTBOX_PROCESSOR_INTERVAL_MS);
  }

  /**
   * Stop the processor
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

    this.logger.info("Outbox processor stopped");
  }

  /**
   * Process pending outbox items
   */
  private async process(): Promise<void> {
    const client = await this.pool.connect();

    try {
      const outboxRepo = new OutboxRepo(client);
      const depositsRepo = new DepositsRepo(client);
      const auditRepo = new AuditRepo(client);

      // Get pending items
      const items = await outboxRepo.getPending(50);

      if (items.length === 0) {
        this.logger.debug("No pending outbox items");
        return;
      }

      this.logger.info({ count: items.length }, "Processing outbox items");

      for (const item of items) {
        const itemLogger = this.logger.child({
          outboxId: item.id,
          depositId: item.depositId,
          userId: item.payload.userId,
        });

        try {
          // Send credit to ledger service
          if (this.nats && !this.nats.isClosed()) {
            await sendDepositCredit(
              this.nats,
              item,
              this.config.LEDGER_SERVICE_NATS_SUBJECT,
              itemLogger
            );
          } else {
            // Fallback to HTTP
            await sendDepositCreditHttp(
              this.config.LEDGER_SERVICE_URL,
              item,
              itemLogger
            );
          }

          // Mark as processed in transaction
          await client.query("BEGIN");
          try {
            await outboxRepo.markProcessed(item.id);

            // Update deposit status to CREDITED
            await depositsRepo.updateStatus(
              item.depositId,
              "CREDITED",
              "credited_at"
            );

            // Log audit event
            await auditRepo.logDeposit(
              "DEPOSIT_CREDITED",
              item.depositId,
              item.payload.userId,
              {
                amountAtoms: item.payload.amountAtoms,
                assetId: item.payload.assetId,
              },
              {
                idempotencyKey: item.idempotencyKey,
                outboxId: item.id,
              }
            );

            await client.query("COMMIT");

            itemLogger.info("Outbox item processed successfully");
          } catch (error) {
            await client.query("ROLLBACK");
            throw error;
          }
        } catch (error) {
          itemLogger.error({ error }, "Failed to process outbox item");

          // Record retry
          try {
            await outboxRepo.recordRetry(item.id, {
              message: error instanceof Error ? error.message : String(error),
              timestamp: new Date().toISOString(),
            });
          } catch (retryError) {
            itemLogger.error(
              { error: retryError },
              "Failed to record retry"
            );
          }

          // If too many retries, log for manual intervention
          if (item.retryCount >= 10) {
            itemLogger.error(
              { retryCount: item.retryCount },
              "Outbox item exceeded max retries"
            );
          }
        }
      }
    } finally {
      client.release();
    }
  }
}
