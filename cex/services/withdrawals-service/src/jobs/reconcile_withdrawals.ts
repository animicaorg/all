/**
 * Reconciliation Job
 * 
 * Cross-check withdrawals vs BitGo transfers and ledger transactions
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { Config } from "../config.js";

export class ReconciliationJob {
  private intervalId: NodeJS.Timeout | null = null;

  constructor(
    private pool: Pool,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Start the job
   */
  start(): void {
    if (this.intervalId) {
      this.logger.warn("Reconciliation job already running");
      return;
    }

    // Run reconciliation every 6 hours
    const intervalMs = 6 * 60 * 60 * 1000;

    this.logger.info(
      { intervalMs },
      "Starting reconciliation job"
    );

    this.intervalId = setInterval(() => {
      this.reconcile().catch((error) => {
        this.logger.error({ error }, "Error in reconciliation job");
      });
    }, intervalMs);

    // Run 5 minutes after startup
    setTimeout(() => {
      this.reconcile().catch((error) => {
        this.logger.error({ error }, "Error in initial reconciliation job run");
      });
    }, 5 * 60 * 1000);
  }

  /**
   * Stop the job
   */
  stop(): void {
    if (this.intervalId) {
      clearInterval(this.intervalId);
      this.intervalId = null;
      this.logger.info("Reconciliation job stopped");
    }
  }

  /**
   * Run reconciliation
   */
  private async reconcile(): Promise<void> {
    this.logger.info("Starting withdrawal reconciliation");

    const client = await this.pool.connect();

    try {
      // 1. Find withdrawals stuck in non-terminal states for > 24 hours
      const stuckQuery = `
        SELECT id, status, provider_ref, created_at, updated_at
        FROM withdrawals
        WHERE status IN ('SIGNING', 'BROADCAST')
          AND updated_at < NOW() - INTERVAL '24 hours'
        ORDER BY updated_at ASC
        LIMIT 100
      `;

      const stuckResult = await client.query(stuckQuery);
      const stuckCount = stuckResult.rowCount ?? stuckResult.rows.length;

      if (stuckCount > 0) {
        this.logger.warn(
          { count: stuckCount },
          "Found withdrawals stuck in non-terminal states"
        );

        // Log stuck withdrawals for manual review
        for (const row of stuckResult.rows) {
          this.logger.warn(
            {
              withdrawalId: row.id,
              status: row.status,
              providerRef: row.provider_ref,
              createdAt: row.created_at,
              updatedAt: row.updated_at,
            },
            "Stuck withdrawal detected"
          );
        }
      }

      // 2. Find withdrawals with missing ledger links
      const missingLinksQuery = `
        SELECT w.id, w.status, w.user_id, w.total_debit_amount
        FROM withdrawals w
        LEFT JOIN withdrawal_ledger_links wll ON w.id = wll.withdrawal_id
        WHERE w.status NOT IN ('REJECTED', 'CANCELED')
          AND wll.withdrawal_id IS NULL
        LIMIT 100
      `;

      const missingLinksResult = await client.query(missingLinksQuery);
      const missingLinksCount = missingLinksResult.rowCount ?? missingLinksResult.rows.length;

      if (missingLinksCount > 0) {
        this.logger.warn(
          { count: missingLinksCount },
          "Found withdrawals with missing ledger links"
        );

        for (const row of missingLinksResult.rows) {
          this.logger.warn(
            {
              withdrawalId: row.id,
              status: row.status,
              userId: row.user_id,
            },
            "Missing ledger link detected"
          );
        }
      }

      // 3. Check for withdrawals marked CONFIRMED but no on-chain txid
      const noTxidQuery = `
        SELECT id, status, provider_ref, confirmed_at
        FROM withdrawals
        WHERE status = 'CONFIRMED'
          AND txid IS NULL
        LIMIT 100
      `;

      const noTxidResult = await client.query(noTxidQuery);
      const noTxidCount = noTxidResult.rowCount ?? noTxidResult.rows.length;

      if (noTxidCount > 0) {
        this.logger.warn(
          { count: noTxidCount },
          "Found confirmed withdrawals without txid"
        );

        for (const row of noTxidResult.rows) {
          this.logger.warn(
            {
              withdrawalId: row.id,
              providerRef: row.provider_ref,
              confirmedAt: row.confirmed_at,
            },
            "Confirmed withdrawal missing txid"
          );
        }
      }

      // Generate summary
      const summary = {
        timestamp: new Date().toISOString(),
        stuckWithdrawals: stuckCount,
        missingLedgerLinks: missingLinksCount,
        confirmedWithoutTxid: noTxidCount,
      };

      this.logger.info(
        summary,
        "Reconciliation completed"
      );

      // TODO: Store reconciliation report in database or send alerts

    } finally {
      client.release();
    }
  }
}
