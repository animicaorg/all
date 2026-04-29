/**
 * Repository for market sequence tracking
 */

import type { Pool, PoolClient } from "pg";

export class SequenceRepo {
  constructor(private client: PoolClient) {}

  /**
   * Get current sequence for a market
   */
  async getCurrentSequence(marketId: string): Promise<bigint> {
    const result = await this.client.query(
      `SELECT last_seq FROM market_sequence WHERE market_id = $1`,
      [marketId]
    );

    if (result.rows.length === 0) {
      // Initialize sequence for new market
      await this.client.query(
        `INSERT INTO market_sequence (market_id, last_seq) VALUES ($1, 0)
         ON CONFLICT (market_id) DO NOTHING`,
        [marketId]
      );
      return 0n;
    }

    return BigInt(result.rows[0].last_seq);
  }

  /**
   * Increment and get next sequence (atomic)
   */
  async nextSequence(marketId: string): Promise<bigint> {
    const result = await this.client.query(
      `INSERT INTO market_sequence (market_id, last_seq, updated_at)
       VALUES ($1, 1, NOW())
       ON CONFLICT (market_id) DO UPDATE
       SET last_seq = market_sequence.last_seq + 1,
           updated_at = NOW()
       RETURNING last_seq`,
      [marketId]
    );

    return BigInt(result.rows[0].last_seq);
  }
}
