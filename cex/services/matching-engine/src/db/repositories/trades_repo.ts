/**
 * Repository for trades
 */

import type { Pool, PoolClient } from "pg";
import { atomsToDecimal } from "../../engine/deterministic.js";
import type { Trade } from "../../engine/types.js";

export class TradesRepo {
  constructor(private client: PoolClient) {}

  /**
   * Insert a trade
   */
  async insertTrade(trade: Trade): Promise<void> {
    const price = atomsToDecimal(trade.priceAtoms, 8);
    const size = atomsToDecimal(trade.sizeAtoms, 8);
    const quoteAmount = atomsToDecimal(trade.quoteAmountAtoms, 10);
    const makerFee = atomsToDecimal(trade.makerFeeAtoms, 10);
    const takerFee = atomsToDecimal(trade.takerFeeAtoms, 10);

    await this.client.query(
      `INSERT INTO trades (
        id, market_id, maker_order_id, taker_order_id,
        price, size, quote_amount, maker_fee, taker_fee,
        fee_asset, fee_bps_maker, fee_bps_taker, sequence, created_at
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
      )`,
      [
        trade.id,
        trade.marketId,
        trade.makerOrderId,
        trade.takerOrderId,
        price,
        size,
        quoteAmount,
        makerFee,
        takerFee,
        trade.feeAsset,
        trade.feeBpsMaker,
        trade.feeBpsTaker,
        trade.sequence.toString(),
        trade.createdAt
      ]
    );
  }

  /**
   * Insert multiple trades in batch
   */
  async insertTrades(trades: Trade[]): Promise<void> {
    if (trades.length === 0) return;

    for (const trade of trades) {
      await this.insertTrade(trade);
    }
  }
}
