/**
 * Backfill Balances Job
 * Recomputes ALL balances from ledger and populates balances_cache
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import { BalancesRepo } from "../db/repositories/index.js";

/**
 * Backfill all balances from ledger entries into balances_cache
 * Safe to run multiple times (idempotent)
 */
export async function backfillBalances(pool: Pool, logger: Logger): Promise<void> {
  const startTime = Date.now();
  let usersProcessed = 0;
  const batchSize = 100;

  logger.info("Starting balance backfill job");

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const balancesRepo = new BalancesRepo(client);

    // Get all distinct user+asset combinations
    const result = await client.query(
      `SELECT DISTINCT user_id, asset_id
       FROM ledger_accounts
       WHERE account_type = 'USER'
       ORDER BY user_id, asset_id`
    );

    const userAssets = result.rows;
    const totalCount = userAssets.length;

    logger.info(`Found ${totalCount} user+asset combinations to backfill`);

    // Process in batches
    for (let i = 0; i < userAssets.length; i++) {
      const { user_id, asset_id } = userAssets[i];

      // Get both AVAILABLE and LOCKED accounts for this user+asset
      const accounts = await client.query(
        `SELECT id, account_name
         FROM ledger_accounts
         WHERE user_id = $1 AND asset_id = $2 AND account_type = 'USER'`,
        [user_id, asset_id]
      );

      let availableAtoms = 0n;
      let lockedAtoms = 0n;

      // Recompute balance for each account type
      for (const account of accounts.rows) {
        const computed = await balancesRepo.recomputeFromLedger(account.id);
        
        if (computed.length > 0) {
          const balance = computed[0].balance;
          
          if (account.account_name === "AVAILABLE") {
            availableAtoms = balance;
          } else if (account.account_name === "LOCKED") {
            lockedAtoms = balance;
          }
        }
      }

      // Update or insert balance cache
      await balancesRepo.updateBalance(
        user_id,
        asset_id,
        availableAtoms,
        lockedAtoms
      );

      usersProcessed++;

      // Log progress
      if (usersProcessed % batchSize === 0 || usersProcessed === totalCount) {
        const percentComplete = ((usersProcessed / totalCount) * 100).toFixed(1);
        logger.info(
          {
            usersProcessed,
            totalCount,
            percentComplete: `${percentComplete}%`
          },
          `Backfill progress: ${usersProcessed}/${totalCount}`
        );
      }
    }

    await client.query("COMMIT");

    const durationMs = Date.now() - startTime;
    logger.info(
      {
        usersProcessed,
        durationMs,
        durationSec: (durationMs / 1000).toFixed(2)
      },
      "Balance backfill completed successfully"
    );
  } catch (error) {
    await client.query("ROLLBACK");
    logger.error({ error }, "Balance backfill failed");
    throw error;
  } finally {
    client.release();
  }
}
