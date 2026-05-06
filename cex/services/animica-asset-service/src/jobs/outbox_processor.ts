/**
 * Animica Deposit Outbox Processor
 *
 * Delivers confirmed Animica deposits to the ledger service. This service only
 * claims deposits produced by provider ANIMICA_NODE so it does not compete for
 * BitGo deposit outbox rows.
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";

type DepositOutboxItem = {
  id: string;
  depositId: string;
  idempotencyKey: string;
  payload: any;
  retryCount: number;
};

function stripTrailingSlash(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

export class AnimicaOutboxProcessor {
  private intervalId: NodeJS.Timeout | null = null;

  constructor(
    private pool: Pool,
    private config: Config,
    private logger: Logger
  ) {}

  start(): void {
    if (this.intervalId) {
      this.logger.warn("Animica outbox processor already running");
      return;
    }

    this.logger.info(
      { intervalMs: this.config.ANIMICA_OUTBOX_PROCESSOR_INTERVAL_MS },
      "Starting Animica outbox processor"
    );

    this.intervalId = setInterval(() => {
      this.process().catch((error) => {
        this.logger.error({ error }, "Animica outbox processor iteration failed");
      });
    }, this.config.ANIMICA_OUTBOX_PROCESSOR_INTERVAL_MS);

    this.process().catch((error) => {
      this.logger.error({ error }, "Animica outbox processor initial run failed");
    });
  }

  stop(): void {
    if (!this.intervalId) return;
    clearInterval(this.intervalId);
    this.intervalId = null;
    this.logger.info("Animica outbox processor stopped");
  }

  private async process(): Promise<void> {
    const client = await this.pool.connect();
    try {
      const items = await this.getPending(client, 50);
      if (items.length === 0) {
        this.logger.debug("No pending Animica deposit outbox items");
        return;
      }

      this.logger.info({ count: items.length }, "Processing Animica deposit outbox items");
      for (const item of items) {
        await this.processItem(client, item);
      }
    } finally {
      client.release();
    }
  }

  private async getPending(client: PoolClient, limit: number): Promise<DepositOutboxItem[]> {
    const result = await client.query(
      `
        SELECT
          deposit_outbox.id::text,
          deposit_outbox.deposit_id::text,
          deposit_outbox.idempotency_key,
          deposit_outbox.payload,
          deposit_outbox.retry_count
        FROM deposit_outbox
        JOIN deposits ON deposits.id = deposit_outbox.deposit_id
        WHERE deposit_outbox.processed_at IS NULL
          AND deposits.provider = 'ANIMICA_NODE'
          AND deposits.asset_network_id = $1::uuid
          AND (deposit_outbox.last_retry_at IS NULL OR deposit_outbox.last_retry_at < NOW() - INTERVAL '30 seconds')
        ORDER BY deposit_outbox.created_at ASC
        LIMIT $2
      `,
      [this.config.ANIMICA_ASSET_NETWORK_ID, limit]
    );

    return result.rows.map((row) => ({
      id: row.id,
      depositId: row.deposit_id,
      idempotencyKey: row.idempotency_key,
      payload: row.payload,
      retryCount: Number(row.retry_count),
    }));
  }

  private async processItem(client: PoolClient, item: DepositOutboxItem): Promise<void> {
    const itemLogger = this.logger.child({
      outboxId: item.id,
      depositId: item.depositId,
      idempotencyKey: item.idempotencyKey,
    });

    try {
      await this.sendToLedger(item);

      await client.query("BEGIN");
      try {
        await client.query(
          `
            UPDATE deposit_outbox
            SET processed_at = NOW(),
                last_error = NULL
            WHERE id = $1
          `,
          [item.id]
        );
        await client.query(
          `
            UPDATE deposits
            SET status = 'CREDITED',
                credited_at = COALESCE(credited_at, NOW()),
                updated_at = NOW()
            WHERE id = $1
              AND status IN ('CONFIRMED', 'CREDITED')
          `,
          [item.depositId]
        );

        await client.query(
          `
            INSERT INTO audit_logs (
              event_type, resource_type, resource_id, user_id, actor_type,
              action, entity_type, entity_id, changes, metadata
            )
            VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10)
          `,
          [
            "DEPOSIT_CREDITED",
            "DEPOSIT",
            item.depositId,
            item.payload.userId,
            "SYSTEM",
            "DEPOSIT_CREDITED",
            "DEPOSIT",
            item.depositId,
            JSON.stringify({
              amountAtoms: item.payload.amountAtoms,
              assetId: item.payload.assetId,
            }),
            JSON.stringify({
              provider: "ANIMICA_NODE",
              txid: item.payload.source?.txid,
              outboxId: item.id,
              idempotencyKey: item.idempotencyKey,
            }),
          ]
        );

        await client.query("COMMIT");
      } catch (error) {
        await client.query("ROLLBACK").catch(() => undefined);
        throw error;
      }

      itemLogger.info("Animica deposit outbox item processed");
    } catch (error) {
      itemLogger.error({ error }, "Failed to process Animica deposit outbox item");
      await client.query(
        `
          UPDATE deposit_outbox
          SET retry_count = retry_count + 1,
              last_retry_at = NOW(),
              last_error = $2
          WHERE id = $1
        `,
        [
          item.id,
          JSON.stringify({
            message: error instanceof Error ? error.message : String(error),
            timestamp: new Date().toISOString(),
          }),
        ]
      );
    }
  }

  private async sendToLedger(item: DepositOutboxItem): Promise<void> {
    const ledgerUrl = stripTrailingSlash(this.config.LEDGER_SERVICE_URL);
    const response = await fetch(`${ledgerUrl}/internal/deposit-credit`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(item.payload),
    });

    if (!response.ok) {
      const body = await response.text().catch(() => "");
      throw new Error(`Ledger service returned ${response.status}: ${body}`);
    }
  }
}
