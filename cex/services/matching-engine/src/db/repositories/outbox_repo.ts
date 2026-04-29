/**
 * Repository for outbox events
 */

import type { Pool, PoolClient } from "pg";
import { v4 as uuidv4 } from "uuid";
import type { OutboxEvent } from "../../engine/types.js";

export class OutboxRepo {
  constructor(private client: PoolClient) {}

  /**
   * Write an event to the outbox
   */
  async writeEvent(event: {
    marketId: string;
    seq: bigint;
    type: "ORDER_EVENT" | "TRADE_EVENT";
    key: string;
    payload: Record<string, any>;
  }): Promise<OutboxEvent> {
    const eventId = uuidv4();
    const createdAt = new Date();

    await this.client.query(
      `INSERT INTO outbox_events (
        id, market_id, seq, type, key, payload, created_at
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7
      )
      ON CONFLICT (key) DO NOTHING`,
      [
        eventId,
        event.marketId,
        event.seq.toString(),
        event.type,
        event.key,
        JSON.stringify(event.payload),
        createdAt
      ]
    );

    return {
      id: eventId,
      marketId: event.marketId,
      seq: event.seq,
      type: event.type,
      key: event.key,
      payload: event.payload,
      createdAt
    };
  }

  /**
   * Get unpublished events
   */
  async getUnpublished(limit: number = 100): Promise<OutboxEvent[]> {
    const result = await this.client.query(
      `SELECT * FROM outbox_events
       WHERE published_at IS NULL
       ORDER BY market_id ASC, seq ASC
       LIMIT $1`,
      [limit]
    );

    return result.rows.map((row) => ({
      id: row.id,
      marketId: row.market_id,
      seq: BigInt(row.seq),
      type: row.type,
      key: row.key,
      payload: row.payload,
      createdAt: new Date(row.created_at),
      publishedAt: row.published_at ? new Date(row.published_at) : undefined
    }));
  }

  /**
   * Mark event as published
   */
  async markPublished(eventId: string): Promise<void> {
    await this.client.query(
      `UPDATE outbox_events
       SET published_at = NOW()
       WHERE id = $1`,
      [eventId]
    );
  }

  /**
   * Mark multiple events as published
   */
  async markPublishedBatch(eventIds: string[]): Promise<void> {
    if (eventIds.length === 0) return;

    await this.client.query(
      `UPDATE outbox_events
       SET published_at = NOW()
       WHERE id = ANY($1)`,
      [eventIds]
    );
  }
}
