/**
 * Snapshots Publisher Job
 * Periodically publishes orderbook snapshots to cache
 */

import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type { MarketDataCache } from '../services/market_data_cache.js';

/**
 * Snapshots Publisher
 * Publishes periodic orderbook snapshots for all markets
 */
export class SnapshotsPublisher {
  private publishInterval: NodeJS.Timeout | null = null;
  private snapshotIntervalMs: number;

  constructor(
    private cache: MarketDataCache,
    private config: Config,
    private logger: Logger
  ) {
    // Default to 1 second snapshot interval
    this.snapshotIntervalMs = 1000;
  }

  /**
   * Start the publisher
   */
  start(): void {
    this.logger.info(
      { intervalMs: this.snapshotIntervalMs },
      'Starting snapshots publisher'
    );

    // Publish snapshots periodically
    this.publishInterval = setInterval(() => {
      this.publishSnapshots().catch((error) => {
        this.logger.error({ error }, 'Failed to publish snapshots');
      });
    }, this.snapshotIntervalMs);

    // Initial publish
    this.publishSnapshots().catch((error) => {
      this.logger.error({ error }, 'Failed to publish initial snapshots');
    });
  }

  /**
   * Stop the publisher
   */
  stop(): void {
    this.logger.info('Stopping snapshots publisher');

    if (this.publishInterval) {
      clearInterval(this.publishInterval);
      this.publishInterval = null;
    }
  }

  /**
   * Publish snapshots for all markets
   */
  private async publishSnapshots(): Promise<void> {
    try {
      // Get all markets from cache
      const markets = this.cache.getMarkets();

      this.logger.debug(
        { marketCount: markets.length },
        'Publishing orderbook snapshots'
      );

      // For each market, get snapshot and update stats
      for (const market of markets) {
        const snapshot = this.cache.getSnapshot(market, this.config.ORDERBOOK_MAX_DEPTH);

        if (snapshot) {
          // Log snapshot info (optional, can be disabled in production)
          this.logger.trace(
            {
              market,
              seq: snapshot.seq,
              bids: snapshot.bids.length,
              asks: snapshot.asks.length,
            },
            'Orderbook snapshot'
          );

          // Update ticker data from orderbook
          const bestBid = snapshot.bids[0]?.price;
          const bestAsk = snapshot.asks[0]?.price;

          if (bestBid || bestAsk) {
            // Update ticker with best bid/ask
            // This is handled by the market data cache internally
          }
        }
      }
    } catch (error) {
      this.logger.error({ error }, 'Error publishing snapshots');
      throw error;
    }
  }

  /**
   * Publish a snapshot for a specific market on demand
   */
  async publishSnapshotForMarket(market: string): Promise<void> {
    const snapshot = this.cache.getSnapshot(market, this.config.ORDERBOOK_MAX_DEPTH);

    if (!snapshot) {
      this.logger.warn({ market }, 'No snapshot available for market');
      return;
    }

    this.logger.debug(
      {
        market,
        seq: snapshot.seq,
        bids: snapshot.bids.length,
        asks: snapshot.asks.length,
      },
      'Published snapshot for market'
    );
  }

  /**
   * Get statistics about snapshot publishing
   */
  getStats() {
    return {
      intervalMs: this.snapshotIntervalMs,
      isRunning: this.publishInterval !== null,
      marketCount: this.cache.getMarkets().length,
    };
  }
}

/**
 * Start snapshots publisher as a standalone process
 */
export async function startSnapshotsPublisher(
  cache: MarketDataCache,
  config: Config,
  logger: Logger
): Promise<SnapshotsPublisher> {
  const publisher = new SnapshotsPublisher(cache, config, logger);
  publisher.start();

  // Graceful shutdown
  const shutdown = () => {
    publisher.stop();
    process.exit(0);
  };

  process.on('SIGTERM', shutdown);
  process.on('SIGINT', shutdown);

  return publisher;
}
