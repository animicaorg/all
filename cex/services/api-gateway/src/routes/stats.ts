import { Router } from "express";
import { Pool } from "pg";

const router = Router();

export function createStatsRouter(pgPool: Pool): any {
  /**
   * GET /stats - Get platform statistics
   * Returns real-time trading volume, active traders, and system uptime
   */
  router.get("/stats", async (_req: any, res) => {
    try {
      // Use a single query with CTEs for better performance
      const result = await pgPool.query(`
        WITH volume AS (
          SELECT COALESCE(SUM(quote_amount), 0) as volume_24h
          FROM trades
          WHERE created_at > NOW() - INTERVAL '24 hours'
        ),
        traders AS (
          SELECT COUNT(DISTINCT user_id) as active_traders
          FROM orders
          WHERE accepted_at > NOW() - INTERVAL '24 hours'
            AND status IN ('PARTIAL_FILL', 'FILLED', 'ACCEPTED')
        ),
        uptime AS (
          SELECT 
            COUNT(CASE WHEN ok = true THEN 1 END)::float / NULLIF(COUNT(*), 0) * 100 as uptime_percentage,
            COUNT(*) as total_checks
          FROM reconciliation_reports
          WHERE job_type = 'BALANCE_RECOMPUTE'
            AND run_at > NOW() - INTERVAL '30 days'
        )
        SELECT 
          volume.volume_24h,
          traders.active_traders,
          uptime.uptime_percentage,
          uptime.total_checks
        FROM volume, traders, uptime
      `);

      const row = result.rows[0];
      
      const stats = {
        volume24h: parseFloat(row.volume_24h || '0'),
        activeTraders: parseInt(row.active_traders || '0'),
        uptimePercentage: row.total_checks > 0 
          ? (row.uptime_percentage !== null ? parseFloat(row.uptime_percentage) : 0)
          : null, // null when no health check data available
      };

      res.json(stats);
    } catch (error) {
      console.error("Error fetching platform stats:", error);
      res.status(500).json({ error: "Failed to fetch platform statistics" });
    }
  });

  return router;
}
