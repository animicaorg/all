/**
 * Reconciliation Job
 * Recomputes all user balances from ledger entries and compares with cached balances
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { ReconciliationReport } from "../domain/types.js";
import { AccountsRepo, BalancesRepo, LedgerRepo } from "../db/repositories/index.js";

interface BalanceMismatch {
  accountId: string;
  userId: string;
  assetId: string;
  accountName: string;
  expected: string;
  actual: string;
  difference: string;
}

/**
 * Run reconciliation: recompute all balances and compare with cache
 */
export async function runReconciliation(
  pool: Pool,
  logger: Logger
): Promise<ReconciliationReport> {
  const startTime = Date.now();
  const mismatches: BalanceMismatch[] = [];
  let accountsChecked = 0;
  let accountsWithMismatches = 0;

  logger.info("Starting reconciliation job");

  const client = await pool.connect();
  try {
    await client.query("BEGIN");

    const accountsRepo = new AccountsRepo(client);
    const balancesRepo = new BalancesRepo(client);

    // Get all user accounts
    const accounts = await client.query(
      `SELECT id, user_id, account_name, asset_id 
       FROM ledger_accounts 
       WHERE account_type = 'USER'
       ORDER BY user_id, asset_id, account_name`
    );

    logger.info(`Found ${accounts.rowCount} user accounts to check`);

    // Check each account
    for (const account of accounts.rows) {
      accountsChecked++;

      // Recompute balance from ledger
      const computed = await balancesRepo.recomputeFromLedger(account.id);
      
      if (computed.length === 0) {
        // No entries for this account, balance should be 0
        continue;
      }

      const computedBalance = computed[0].balance;

      // Get cached balance
      const cached = await balancesRepo.getBalance(account.user_id, account.asset_id);
      
      let cachedBalance = 0n;
      if (cached) {
        cachedBalance = account.account_name === "AVAILABLE" 
          ? cached.availableAtoms 
          : cached.lockedAtoms;
      }

      // Compare
      if (computedBalance !== cachedBalance) {
        accountsWithMismatches++;
        mismatches.push({
          accountId: account.id,
          userId: account.user_id,
          assetId: account.asset_id,
          accountName: account.account_name,
          expected: computedBalance.toString(),
          actual: cachedBalance.toString(),
          difference: (computedBalance - cachedBalance).toString()
        });

        logger.warn(
          {
            accountId: account.id,
            userId: account.user_id,
            assetId: account.asset_id,
            accountName: account.account_name,
            expected: computedBalance.toString(),
            actual: cachedBalance.toString()
          },
          "Balance mismatch detected"
        );
      }

      if (accountsChecked % 1000 === 0) {
        logger.info(`Progress: checked ${accountsChecked} accounts`);
      }
    }

    // Fix mismatches if AUTO_FIX is enabled
    const autoFix = process.env.AUTO_FIX === "true";
    if (autoFix && mismatches.length > 0) {
      logger.info(`AUTO_FIX enabled, fixing ${mismatches.length} mismatches`);
      
      for (const mismatch of mismatches) {
        // Recompute and update all balances for this user+asset
        const userAccounts = await client.query(
          `SELECT id, account_name
           FROM ledger_accounts
           WHERE user_id = $1 AND asset_id = $2 AND account_type = 'USER'`,
          [mismatch.userId, mismatch.assetId]
        );

        let availableAtoms = 0n;
        let lockedAtoms = 0n;

        for (const acc of userAccounts.rows) {
          const computed = await balancesRepo.recomputeFromLedger(acc.id);
          if (computed.length > 0) {
            if (acc.account_name === "AVAILABLE") {
              availableAtoms = computed[0].balance;
            } else if (acc.account_name === "LOCKED") {
              lockedAtoms = computed[0].balance;
            }
          }
        }

        await balancesRepo.updateBalance(
          mismatch.userId,
          mismatch.assetId,
          availableAtoms,
          lockedAtoms
        );

        logger.info(
          { userId: mismatch.userId, assetId: mismatch.assetId },
          "Fixed balance mismatch"
        );
      }
    }

    // Write report to database
    const report = await client.query(
      `INSERT INTO reconciliation_reports 
       (job_type, ok, mismatches, summary, run_at)
       VALUES ($1, $2, $3, $4, NOW())
       RETURNING id, job_type, ok, mismatches, summary, run_at`,
      [
        "BALANCE_RECOMPUTE",
        mismatches.length === 0,
        JSON.stringify(mismatches),
        JSON.stringify({
          accountsChecked,
          accountsWithMismatches,
          autoFixEnabled: autoFix,
          durationMs: Date.now() - startTime
        })
      ]
    );

    await client.query("COMMIT");

    const result: ReconciliationReport = {
      id: report.rows[0].id,
      jobType: report.rows[0].job_type,
      ok: report.rows[0].ok,
      mismatches: mismatches.map(m => ({
        accountId: m.accountId,
        assetId: m.assetId,
        expected: m.expected,
        actual: m.actual
      })),
      summary: report.rows[0].summary,
      runAt: report.rows[0].run_at
    };

    logger.info(
      {
        accountsChecked,
        accountsWithMismatches,
        autoFixEnabled: autoFix,
        durationMs: Date.now() - startTime
      },
      "Reconciliation job completed"
    );

    return result;
  } catch (error) {
    await client.query("ROLLBACK");
    logger.error({ error }, "Reconciliation job failed");
    throw error;
  } finally {
    client.release();
  }
}
