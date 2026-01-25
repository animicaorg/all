/**
 * Database transaction helper
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";

/**
 * Execute a function within a database transaction
 */
export async function withTransaction<T>(
  pool: Pool,
  fn: (client: PoolClient) => Promise<T>,
  logger?: Logger
): Promise<T> {
  const client = await pool.connect();
  
  try {
    await client.query("BEGIN");
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    logger?.error({ error }, "Transaction rolled back");
    throw error;
  } finally {
    client.release();
  }
}
