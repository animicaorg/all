/**
 * Public Tickers Endpoint
 * GET /api/v1/tickers
 */

import { Router } from 'express';
import { z } from 'zod';
import type { Logger } from '../../utils/logger.js';
import { validate } from '../middleware/validation.js';
import { MarketDataCache, type Ticker } from '../../services/market_data_cache.js';

const tickersQuerySchema = z.object({
  market: z.string().optional(), // If not provided, return all markets
});

export interface TickerResponse {
  market: string;
  last: string;
  best_bid: string;
  best_ask: string;
  mid: string;
  volume_24h: string;
  quote_volume_24h: string;
  change_24h: string;
  high_24h: string;
  low_24h: string;
  ts: number;
}

export function createPublicTickersRouter(
  cache: MarketDataCache,
  logger: Logger
): Router {
  const router = Router();

  /**
   * GET /api/v1/tickers
   * Returns ticker data for one or all markets
   */
  router.get(
    '/',
    validate({ query: tickersQuerySchema }),
    async (req, res, next) => {
      try {
        const { market } = (req as any).validated.query;

        if (market) {
          // Single market ticker
          const ticker = cache.getTicker(market);
          if (!ticker) {
            return res.json(null);
          }

          const response = formatTicker(market, ticker);
          res.json(response);
        } else {
          // All markets tickers
          const tickers = cache.getAllTickers();
          const response = Object.entries(tickers).map(([market, ticker]) =>
            formatTicker(market, ticker)
          );
          res.json(response);
        }
      } catch (error) {
        logger.error({ error }, 'Failed to fetch tickers');
        next(error);
      }
    }
  );

  return router;
}

function formatTicker(market: string, ticker: Ticker): TickerResponse {
  const mid =
    ticker.bestBid && ticker.bestAsk
      ? (
          (parseFloat(ticker.bestBid) + parseFloat(ticker.bestAsk)) /
          2
        ).toString()
      : ticker.lastPrice || '0';

  return {
    market,
    last: ticker.lastPrice || '0',
    best_bid: ticker.bestBid || '0',
    best_ask: ticker.bestAsk || '0',
    mid,
    volume_24h: ticker.volume24h || '0',
    quote_volume_24h: ticker.quoteVolume24h || '0',
    change_24h: ticker.change24h || '0',
    high_24h: ticker.high24h || '0',
    low_24h: ticker.low24h || '0',
    ts: ticker.timestamp,
  };
}
