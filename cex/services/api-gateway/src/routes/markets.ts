import { Router } from "express";
import { Pool } from "pg";
import { z } from "zod";

const router = Router();

export function createMarketsRouter(pgPool: Pool): any {
  /**
   * GET /markets - List all active markets
   */
  router.get("/markets", async (_req: any, res) => {
    try {
      const result = await pgPool.query(`
        SELECT 
          m.symbol,
          m.base_asset,
          m.quote_asset,
          m.price_tick,
          m.size_step,
          m.min_order_size,
          m.maker_fee_bps,
          m.taker_fee_bps,
          m.fee_asset,
          m.active,
          COALESCE(t.last_price, 0) as last_price,
          COALESCE(t.volume_24h, 0) as volume_24h,
          COALESCE(t.high_24h, 0) as high_24h,
          COALESCE(t.low_24h, 0) as low_24h,
          COALESCE(t.price_change_24h, 0) as price_change_24h
        FROM markets m
        LEFT JOIN LATERAL (
          SELECT 
            price as last_price,
            SUM(size) as volume_24h,
            MAX(price) as high_24h,
            MIN(price) as low_24h,
            (MAX(price) - MIN(price)) / NULLIF(MIN(price), 0) * 100 as price_change_24h
          FROM trades
          WHERE market_id = m.id
            AND created_at > NOW() - INTERVAL '24 hours'
        ) t ON true
        WHERE m.active = true
        ORDER BY m.symbol
      `);

      res.json({
        markets: result.rows.map(row => ({
          symbol: row.symbol,
          baseAsset: row.base_asset,
          quoteAsset: row.quote_asset,
          priceTick: parseFloat(row.price_tick),
          sizeStep: parseFloat(row.size_step),
          minOrderSize: parseFloat(row.min_order_size),
          makerFeeBps: parseInt(row.maker_fee_bps),
          takerFeeBps: parseInt(row.taker_fee_bps),
          feeAsset: row.fee_asset,
          lastPrice: parseFloat(row.last_price) || 0,
          volume24h: parseFloat(row.volume_24h) || 0,
          high24h: parseFloat(row.high_24h) || 0,
          low24h: parseFloat(row.low_24h) || 0,
          priceChange24h: parseFloat(row.price_change_24h) || 0,
        })),
      });
    } catch (error) {
      console.error("Error fetching markets:", error);
      res.status(500).json({ error: "Failed to fetch markets" });
    }
  });

  /**
   * GET /markets/:symbol/orderbook - Get orderbook for a market
   */
  router.get("/markets/:symbol/orderbook", async (req: any, res) => {
    try {
      const { symbol } = req.params;
      const limit = parseInt(req.query.limit as string) || 20;

      // Get market ID
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return res.status(404).json({ error: "Market not found" });
      }

      const marketId = marketResult.rows[0].id;

      // Get bids and asks
      const ordersResult = await pgPool.query(
        `
        SELECT 
          side,
          price,
          SUM(remaining_quantity) as total_quantity
        FROM orders
        WHERE market_id = $1
          AND status = 'ACCEPTED'
          AND remaining_quantity > 0
        GROUP BY side, price
        ORDER BY 
          CASE WHEN side = 'buy' THEN price END DESC,
          CASE WHEN side = 'sell' THEN price END ASC
      `,
        [marketId]
      );

      const bids: Array<{ price: number; quantity: number; total: number }> =
        [];
      const asks: Array<{ price: number; quantity: number; total: number }> =
        [];
      let bidTotal = 0;
      let askTotal = 0;

      ordersResult.rows.forEach((row: any) => {
        const price = parseFloat(row.price);
        const quantity = parseFloat(row.total_quantity);

        if (row.side === "buy") {
          bidTotal += quantity;
          if (bids.length < limit) {
            bids.push({ price, quantity, total: bidTotal });
          }
        } else {
          askTotal += quantity;
          if (asks.length < limit) {
            asks.push({ price, quantity, total: askTotal });
          }
        }
      });

      // Get latest sequence
      const seqResult = await pgPool.query(
        "SELECT last_seq FROM market_sequence WHERE market_id = $1",
        [marketId]
      );
      const sequence = seqResult.rows[0]?.last_seq || 0;

      res.json({
        symbol,
        bids,
        asks,
        sequence: parseInt(sequence),
        timestamp: Date.now(),
      });
    } catch (error) {
      console.error("Error fetching orderbook:", error);
      res.status(500).json({ error: "Failed to fetch orderbook" });
    }
  });

  /**
   * GET /markets/:symbol/trades - Get recent trades for a market
   */
  router.get("/markets/:symbol/trades", async (req: any, res) => {
    try {
      const { symbol } = req.params;
      const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);

      // Get market ID
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return res.status(404).json({ error: "Market not found" });
      }

      const marketId = marketResult.rows[0].id;

      // Get trades
      const tradesResult = await pgPool.query(
        `
        SELECT 
          t.id,
          t.price,
          t.size as quantity,
          t.sequence,
          t.created_at,
          CASE 
            WHEN taker_order.side = 'buy' THEN 'buy'
            ELSE 'sell'
          END as side
        FROM trades t
        JOIN orders taker_order ON t.taker_order_id = taker_order.id
        WHERE t.market_id = $1
        ORDER BY t.sequence DESC
        LIMIT $2
      `,
        [marketId, limit]
      );

      res.json({
        symbol,
        trades: tradesResult.rows.map((row: any) => ({
          id: row.id,
          price: parseFloat(row.price),
          quantity: parseFloat(row.quantity),
          side: row.side,
          sequence: parseInt(row.sequence),
          timestamp: new Date(row.created_at).getTime(),
        })),
      });
    } catch (error) {
      console.error("Error fetching trades:", error);
      res.status(500).json({ error: "Failed to fetch trades" });
    }
  });

  /**
   * GET /markets/:symbol/candles - Get candlestick data
   */
  router.get("/markets/:symbol/candles", async (req: any, res) => {
    try {
      const { symbol } = req.params;
      const resolution = (req.query.resolution as string) || "1m";
      const limit = Math.min(parseInt(req.query.limit as string) || 500, 1000);

      // Map resolution to interval
      const intervalMap: Record<string, string> = {
        "1m": "1 minute",
        "5m": "5 minutes",
        "15m": "15 minutes",
        "1h": "1 hour",
        "4h": "4 hours",
        "1d": "1 day",
      };

      const interval = intervalMap[resolution] || "1 minute";

      // Get market ID
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return res.status(404).json({ error: "Market not found" });
      }

      const marketId = marketResult.rows[0].id;

      // Generate candles from trades
      const candlesResult = await pgPool.query(
        `
        SELECT 
          time_bucket($1::interval, created_at) as bucket,
          (array_agg(price ORDER BY created_at ASC))[1] as open,
          MAX(price) as high,
          MIN(price) as low,
          (array_agg(price ORDER BY created_at DESC))[1] as close,
          SUM(size) as volume
        FROM trades
        WHERE market_id = $2
          AND created_at > NOW() - ($1::interval * $3)
        GROUP BY bucket
        ORDER BY bucket DESC
        LIMIT $3
      `,
        [interval, marketId, limit]
      );

      res.json({
        symbol,
        resolution,
        candles: candlesResult.rows.map((row: any) => ({
          timestamp: new Date(row.bucket).getTime(),
          open: parseFloat(row.open),
          high: parseFloat(row.high),
          low: parseFloat(row.low),
          close: parseFloat(row.close),
          volume: parseFloat(row.volume),
        })),
      });
    } catch (error) {
      console.error("Error fetching candles:", error);
      // If time_bucket function doesn't exist (TimescaleDB not installed),
      // return a fallback or approximate candles
      res.status(500).json({
        error: "Candlestick data temporarily unavailable",
        message:
          "Install TimescaleDB extension or use trade data to approximate",
      });
    }
  });

  return router;
}
