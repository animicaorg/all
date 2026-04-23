/**
 * Database connection utilities
 */

import { Pool } from "pg";
import type { PoolClient } from "pg";
import type { Logger } from "pino";

export { Pool };
export type { PoolClient };

/**
 * Execute a query within a transaction
 */
export async function withTransaction<T>(
  pool: Pool,
  callback: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await callback(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Execute a query within a transaction with logging
 */
export async function transact<T>(
  pool: Pool,
  logger: Logger,
  callback: (client: PoolClient) => Promise<T>
): Promise<T> {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    const result = await callback(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    logger.error({ error }, "Transaction rolled back");
    throw error;
  } finally {
    client.release();
  }
}
