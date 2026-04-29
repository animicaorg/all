/**
 * Repository for market configuration
 */

import type { Pool, PoolClient } from "pg";
import { decimalToAtoms } from "../../engine/deterministic.js";
import type { MarketConfig } from "../../engine/types.js";

export class MarketRepo {
  constructor(private client: PoolClient) {}

  async getById(marketId: string): Promise<MarketConfig | null> {
    const result = await this.client.query(
      `SELECT * FROM markets WHERE id = $1`,
      [marketId]
    );

    if (result.rows.length === 0) return null;

    const row = result.rows[0];
    return {
      id: row.id,
      symbol: row.symbol,
      baseAsset: row.base_asset,
      quoteAsset: row.quote_asset,
      priceTick: decimalToAtoms(row.price_tick, 8),
      sizeStep: decimalToAtoms(row.size_step, 8),
      minOrderSize: decimalToAtoms(row.min_order_size, 8),
      makerFeeBps: row.maker_fee_bps,
      takerFeeBps: row.taker_fee_bps,
      feeAsset: row.fee_asset,
      active: row.active
    };
  }

  async getBySymbol(symbol: string): Promise<MarketConfig | null> {
    const result = await this.client.query(
      `SELECT * FROM markets WHERE symbol = $1`,
      [symbol]
    );

    if (result.rows.length === 0) return null;

    const row = result.rows[0];
    return {
      id: row.id,
      symbol: row.symbol,
      baseAsset: row.base_asset,
      quoteAsset: row.quote_asset,
      priceTick: decimalToAtoms(row.price_tick, 8),
      sizeStep: decimalToAtoms(row.size_step, 8),
      minOrderSize: decimalToAtoms(row.min_order_size, 8),
      makerFeeBps: row.maker_fee_bps,
      takerFeeBps: row.taker_fee_bps,
      feeAsset: row.fee_asset,
      active: row.active
    };
  }
}
