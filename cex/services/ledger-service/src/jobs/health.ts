/**
 * Health Check Job
 * Monitors system health and data integrity
 */

import type { Pool } from "pg";
import type { Logger } from "pino";

/**
 * Health status result
 */
export interface HealthStatus {
  ok: boolean;
  timestamp: Date;
  checks: {
    database: HealthCheck;
    sequenceGaps: HealthCheck;
    negativeBalances: HealthCheck;
    recentReconciliation: HealthCheck;
  };
  summary: string;
}

interface HealthCheck {
  ok: boolean;
  message: string;
  details?: Record<string, unknown>;
}

/**
 * Run comprehensive health checks
 */
export async function checkHealth(pool: Pool, logger: Logger): Promise<HealthStatus> {
  logger.info("Starting health check");

  const checks: HealthStatus["checks"] = {
    database: { ok: false, message: "" },
    sequenceGaps: { ok: false, message: "" },
    negativeBalances: { ok: false, message: "" },
    recentReconciliation: { ok: false, message: "" }
  };

  const client = await pool.connect();
  try {
    // Check 1: Database connection
    try {
      await client.query("SELECT 1");
      checks.database = {
        ok: true,
        message: "Database connection healthy"
      };
    } catch (error) {
      checks.database = {
        ok: false,
        message: `Database connection failed: ${error}`
      };
    }

    // Check 2: Sequence gaps per market
    try {
      const gapsResult = await client.query(
        `WITH seq_ranges AS (
          SELECT 
            market_id,
            MIN(seq) as min_seq,
            MAX(seq) as max_seq,
            COUNT(DISTINCT seq) as count_seq
          FROM ledger_transactions
          WHERE market_id IS NOT NULL AND seq IS NOT NULL
          GROUP BY market_id
        )
        SELECT 
          market_id,
          min_seq,
          max_seq,
          count_seq,
          (max_seq - min_seq + 1) as expected_count,
          ((max_seq - min_seq + 1) - count_seq) as gap_count
        FROM seq_ranges
        WHERE (max_seq - min_seq + 1) != count_seq`
      );

      if (gapsResult.rowCount === 0) {
        checks.sequenceGaps = {
          ok: true,
          message: "No sequence gaps detected"
        };
      } else {
        const gaps = gapsResult.rows.map(row => ({
          marketId: row.market_id,
          minSeq: row.min_seq,
          maxSeq: row.max_seq,
          gapCount: parseInt(row.gap_count)
        }));

        checks.sequenceGaps = {
          ok: false,
          message: `Found sequence gaps in ${gapsResult.rowCount} market(s)`,
          details: { gaps }
        };
      }
    } catch (error) {
      checks.sequenceGaps = {
        ok: false,
        message: `Sequence gap check failed: ${error}`
      };
    }

    // Check 3: Negative balances
    try {
      const negativeResult = await client.query(
        `WITH account_balances AS (
          SELECT 
            le.account_id,
            la.user_id,
            la.asset_id,
            la.account_name,
            COALESCE(SUM(
              CASE 
                WHEN le.direction = 'DEBIT' THEN le.amount_atoms
                WHEN le.direction = 'CREDIT' THEN -le.amount_atoms
              END
            ), 0) as balance
          FROM ledger_entries le
          JOIN ledger_accounts la ON la.id = le.account_id
          WHERE la.account_type = 'USER'
          GROUP BY le.account_id, la.user_id, la.asset_id, la.account_name
        )
        SELECT 
          account_id,
          user_id,
          asset_id,
          account_name,
          balance
        FROM account_balances
        WHERE balance < 0
        LIMIT 10`
      );

      if (negativeResult.rowCount === 0) {
        checks.negativeBalances = {
          ok: true,
          message: "No negative balances detected"
        };
      } else {
        const negatives = negativeResult.rows.map(row => ({
          accountId: row.account_id,
          userId: row.user_id,
          assetId: row.asset_id,
          accountName: row.account_name,
          balance: row.balance
        }));

        checks.negativeBalances = {
          ok: false,
          message: `Found ${negativeResult.rowCount} accounts with negative balances`,
          details: { negatives }
        };
      }
    } catch (error) {
      checks.negativeBalances = {
        ok: false,
        message: `Negative balance check failed: ${error}`
      };
    }

    // Check 4: Recent reconciliation status
    try {
      const reconResult = await client.query(
        `SELECT id, job_type, ok, summary, run_at
         FROM reconciliation_reports
         WHERE job_type = 'BALANCE_RECOMPUTE'
         ORDER BY run_at DESC
         LIMIT 1`
      );

      if (reconResult.rowCount === 0) {
        checks.recentReconciliation = {
          ok: false,
          message: "No reconciliation reports found"
        };
      } else {
        const report = reconResult.rows[0];
        const runAt = new Date(report.run_at);
        const ageHours = (Date.now() - runAt.getTime()) / (1000 * 60 * 60);

        if (ageHours > 24) {
          checks.recentReconciliation = {
            ok: false,
            message: `Last reconciliation was ${ageHours.toFixed(1)} hours ago (>24h)`,
            details: {
              lastRunAt: runAt.toISOString(),
              lastReportOk: report.ok
            }
          };
        } else if (!report.ok) {
          checks.recentReconciliation = {
            ok: false,
            message: "Last reconciliation found mismatches",
            details: {
              lastRunAt: runAt.toISOString(),
              summary: report.summary
            }
          };
        } else {
          checks.recentReconciliation = {
            ok: true,
            message: `Last reconciliation was ${ageHours.toFixed(1)} hours ago and passed`,
            details: {
              lastRunAt: runAt.toISOString()
            }
          };
        }
      }
    } catch (error) {
      checks.recentReconciliation = {
        ok: false,
        message: `Reconciliation check failed: ${error}`
      };
    }

    // Overall health status
    const allOk = Object.values(checks).every(check => check.ok);
    const failedChecks = Object.entries(checks)
      .filter(([_, check]) => !check.ok)
      .map(([name, _]) => name);

    const summary = allOk
      ? "All health checks passed"
      : `Health checks failed: ${failedChecks.join(", ")}`;

    const status: HealthStatus = {
      ok: allOk,
      timestamp: new Date(),
      checks,
      summary
    };

    logger.info(
      {
        ok: allOk,
        failedChecks: failedChecks.length > 0 ? failedChecks : undefined
      },
      "Health check completed"
    );

    return status;
  } catch (error) {
    logger.error({ error }, "Health check failed");
    throw error;
  } finally {
    client.release();
  }
}
