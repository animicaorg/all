/**
 * Repository for idempotency keys
 */

import type { Pool, PoolClient } from "pg";

export class IdempotencyRepo {
  constructor(private client: PoolClient) {}

  /**
   * Check if idempotency key exists and return cached result
   */
  async get(key: string, consumer: string): Promise<Record<string, any> | null> {
    const result = await this.client.query(
      `SELECT result FROM idempotency_keys
       WHERE key = $1 AND consumer = $2
       AND (expires_at IS NULL OR expires_at > NOW())`,
      [key, consumer]
    );

    if (result.rows.length === 0) return null;
    return result.rows[0].result;
  }

  /**
   * Store idempotency result
   */
  async set(
    key: string,
    consumer: string,
    result: Record<string, any>,
    ttlSeconds: number = 86400
  ): Promise<void> {
    const expiresAt = new Date(Date.now() + ttlSeconds * 1000);

    await this.client.query(
      `INSERT INTO idempotency_keys (key, consumer, result, created_at, expires_at)
       VALUES ($1, $2, $3, NOW(), $4)
       ON CONFLICT (key) DO UPDATE
       SET result = $3, expires_at = $4`,
      [key, consumer, JSON.stringify(result), expiresAt]
    );
  }

  /**
   * Delete expired keys
   */
  async deleteExpired(): Promise<number> {
    const result = await this.client.query(
      `DELETE FROM idempotency_keys
       WHERE expires_at IS NOT NULL AND expires_at < NOW()`
    );

    return result.rowCount || 0;
  }
}
