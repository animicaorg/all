/**
 * Outbox publisher
 * Polls outbox table and publishes events to NATS
 */

import type { NatsConnection } from "nats";
import type { Pool, PoolClient } from "pg";
import { jsonCodec } from "@cex/common";
import type { Logger } from "pino";
import { OutboxRepo } from "../db/repositories/index.js";

export class OutboxPublisher {
  private running = false;
  private intervalMs: number;

  constructor(
    private pool: Pool,
    private nats: NatsConnection,
    private logger: Logger,
    intervalMs: number = 1000
  ) {
    this.intervalMs = intervalMs;
  }

  /**
   * Start publishing loop
   */
  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.logger.info("Starting outbox publisher");

    while (this.running) {
      try {
        await this.publishBatch();
      } catch (error) {
        this.logger.error({ error }, "Error publishing outbox batch");
      }

      // Wait before next poll
      await new Promise((resolve) => setTimeout(resolve, this.intervalMs));
    }
  }

  /**
   * Stop publishing loop
   */
  stop(): void {
    this.running = false;
    this.logger.info("Stopping outbox publisher");
  }

  /**
   * Publish a batch of events
   */
  private async publishBatch(): Promise<void> {
    const client = await this.pool.connect();
    try {
      const repo = new OutboxRepo(client);
      const events = await repo.getUnpublished(100);

      if (events.length === 0) return;

      this.logger.debug({ count: events.length }, "Publishing outbox events");

      const published: string[] = [];

      for (const event of events) {
        try {
          const subject = this.getSubject(event.type, event.marketId);
          await this.nats.publish(subject, jsonCodec.encode(event.payload));
          published.push(event.id);
        } catch (error) {
          this.logger.error(
            { error, eventId: event.id, type: event.type },
            "Failed to publish event"
          );
        }
      }

      // Mark as published in a transaction
      if (published.length > 0) {
        await repo.markPublishedBatch(published);
        this.logger.info({ count: published.length }, "Marked events as published");
      }
    } finally {
      client.release();
    }
  }

  /**
   * Get NATS subject for event type
   */
  private getSubject(type: "ORDER_EVENT" | "TRADE_EVENT", marketId: string): string {
    if (type === "ORDER_EVENT") {
      return `cex.order.event.${marketId}`;
    } else {
      return `cex.trade.event.${marketId}`;
    }
  }
}
