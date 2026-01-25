/**
 * Idempotency Repository
 * Manages event processing idempotency and offsets
 */

import type { PoolClient } from "pg";
import type { LedgerEventOffset } from "../../domain/types.js";

export class IdempotencyRepo {
  constructor(private client: PoolClient) {}

  /**
   * Get last processed sequences for a market
   */
  async getOffset(marketId: string): Promise<LedgerEventOffset | null> {
    const result = await this.client.query(
      `SELECT market_id, consumer_group, last_trade_seq, last_order_seq, updated_at
       FROM ledger_event_offsets
       WHERE market_id = $1`,
      [marketId]
    );

    if (result.rowCount === 0) return null;

    return this.mapOffsetRow(result.rows[0]);
  }

  /**
   * Update processed sequences for a market
   * Only updates provided sequences (tradeSeq or orderSeq can be omitted)
   */
  async updateOffset(
    marketId: string,
    tradeSeq?: bigint,
    orderSeq?: bigint
  ): Promise<LedgerEventOffset> {
    if (tradeSeq === undefined && orderSeq === undefined) {
      throw new Error("updateOffset called with no sequences to update");
    }

    // Build INSERT and UPDATE clauses
    const insertValues: string[] = ['$1', "'ledger-service'"];
    const updates: string[] = ['updated_at = NOW()'];
    const params: any[] = [marketId];
    let paramIndex = 2;

    if (tradeSeq !== undefined) {
      insertValues.push(`$${paramIndex}`);
      updates.push(`last_trade_seq = $${paramIndex}`);
      params.push(tradeSeq.toString());
      paramIndex++;
    } else {
      insertValues.push('0');
    }

    if (orderSeq !== undefined) {
      insertValues.push(`$${paramIndex}`);
      updates.push(`last_order_seq = $${paramIndex}`);
      params.push(orderSeq.toString());
      paramIndex++;
    } else {
      insertValues.push('0');
    }

    const result = await this.client.query(
      `INSERT INTO ledger_event_offsets (market_id, consumer_group, last_trade_seq, last_order_seq)
       VALUES (${insertValues.join(', ')})
       ON CONFLICT (market_id)
       DO UPDATE SET ${updates.join(', ')}
       RETURNING market_id, consumer_group, last_trade_seq, last_order_seq, updated_at`,
      params
    );

    return this.mapOffsetRow(result.rows[0]);
  }

  /**
   * Check if an event has already been processed
   */
  async checkProcessed(key: string): Promise<boolean> {
    const result = await this.client.query(
      `SELECT 1 FROM processed_events WHERE event_id = $1`,
      [key]
    );

    return (result.rowCount ?? 0) > 0;
  }

  /**
   * Mark an event as processed
   * Result parameter can store any metadata about the processing
   */
  async markProcessed(key: string, result: Record<string, unknown> = {}): Promise<void> {
    await this.client.query(
      `INSERT INTO processed_events (event_id, consumer)
       VALUES ($1, $2)
       ON CONFLICT (event_id) DO NOTHING`,
      [key, 'ledger-service']
    );
  }

  /**
   * Check multiple event keys at once
   * Returns a Set of keys that have been processed
   */
  async checkMultipleProcessed(keys: string[]): Promise<Set<string>> {
    if (keys.length === 0) return new Set();

    const result = await this.client.query(
      `SELECT event_id FROM processed_events WHERE event_id = ANY($1)`,
      [keys]
    );

    return new Set(result.rows.map((row) => row.event_id));
  }

  /**
   * Get idempotency key (for deposit credits, etc.)
   */
  async get(key: string): Promise<{ result: any; createdAt: Date } | null> {
    const result = await this.client.query(
      `SELECT result, created_at FROM idempotency_keys WHERE key = $1`,
      [key]
    );

    if (result.rowCount === 0) return null;

    return {
      result: result.rows[0].result,
      createdAt: result.rows[0].created_at,
    };
  }

  /**
   * Set idempotency key with result
   */
  async set(
    key: string,
    consumer: string,
    result: Record<string, unknown>,
    ttlSeconds?: number
  ): Promise<void> {
    const expiresAt = ttlSeconds
      ? new Date(Date.now() + ttlSeconds * 1000)
      : null;

    await this.client.query(
      `INSERT INTO idempotency_keys (key, consumer, result, expires_at)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (key) DO NOTHING`,
      [key, consumer, JSON.stringify(result), expiresAt]
    );
  }

  private mapOffsetRow(row: any): LedgerEventOffset {
    return {
      marketId: row.market_id,
      consumerGroup: row.consumer_group,
      lastTradeSeq: BigInt(row.last_trade_seq),
      lastOrderSeq: BigInt(row.last_order_seq),
      updatedAt: row.updated_at
    };
  }
}
