import { Router } from "express";
import { Pool } from "pg";
import { z } from "zod";

const router = Router();

const resolutionToMs: Record<string, number> = {
  "1m": 60 * 1000,
  "5m": 5 * 60 * 1000,
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
  "4h": 4 * 60 * 60 * 1000,
  "1d": 24 * 60 * 60 * 1000,
};

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
            (array_agg(price ORDER BY created_at DESC))[1] as last_price,
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
          AND status IN ('ACCEPTED', 'PARTIAL_FILL')
          AND remaining_quantity > 0
        GROUP BY side, price
        ORDER BY 
          CASE WHEN lower(side) = 'buy' THEN price END DESC,
          CASE WHEN lower(side) = 'sell' THEN price END ASC
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

        if (String(row.side).toLowerCase() === "buy") {
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
            WHEN lower(taker_order.side) = 'buy' THEN 'buy'
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

      const bucketMs = resolutionToMs[resolution] || resolutionToMs["1m"];
      const fromDate = new Date(Date.now() - bucketMs * limit);

      // Get market ID
      const marketResult = await pgPool.query(
        "SELECT id FROM markets WHERE symbol = $1 AND active = true",
        [symbol]
      );

      if (marketResult.rows.length === 0) {
        return res.status(404).json({ error: "Market not found" });
      }

      const marketId = marketResult.rows[0].id;

      const tradesResult = await pgPool.query(
        `
        SELECT 
          created_at,
          price,
          size
        FROM trades
        WHERE market_id = $1
          AND created_at > $2
        ORDER BY created_at ASC
      `,
        [marketId, fromDate]
      );

      const candleMap = new Map<
        number,
        { timestamp: number; open: number; high: number; low: number; close: number; volume: number }
      >();

      for (const row of tradesResult.rows) {
        const timestamp = new Date(row.created_at).getTime();
        const bucket = Math.floor(timestamp / bucketMs) * bucketMs;
        const price = parseFloat(row.price);
        const size = parseFloat(row.size);
        const candle = candleMap.get(bucket);

        if (!candle) {
          candleMap.set(bucket, {
            timestamp: bucket,
            open: price,
            high: price,
            low: price,
            close: price,
            volume: size,
          });
        } else {
          candle.high = Math.max(candle.high, price);
          candle.low = Math.min(candle.low, price);
          candle.close = price;
          candle.volume += size;
        }
      }

      res.json({
        symbol,
        resolution,
        candles: [...candleMap.values()].slice(-limit),
      });
    } catch (error) {
      console.error("Error fetching candles:", error);
      res.status(500).json({ error: "Failed to fetch candles" });
    }
  });

  return router;
}
