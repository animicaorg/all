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
      `SELECT
         m.*,
         base_asset.decimals AS base_decimals,
         quote_asset.decimals AS quote_decimals,
         fee_asset.decimals AS fee_decimals
       FROM markets m
       LEFT JOIN assets base_asset ON UPPER(base_asset.symbol) = UPPER(m.base_asset)
       LEFT JOIN assets quote_asset ON UPPER(quote_asset.symbol) = UPPER(m.quote_asset)
       LEFT JOIN assets fee_asset ON UPPER(fee_asset.symbol) = UPPER(m.fee_asset)
       WHERE m.id = $1`,
      [marketId]
    );

    if (result.rows.length === 0) return null;

    const row = result.rows[0];
    const baseDecimals = row.base_decimals == null ? 8 : Number(row.base_decimals);
    return {
      id: row.id,
      symbol: row.symbol,
      baseAsset: row.base_asset,
      quoteAsset: row.quote_asset,
      priceTick: decimalToAtoms(row.price_tick, 8),
      sizeStep: decimalToAtoms(row.size_step, baseDecimals),
      minOrderSize: decimalToAtoms(row.min_order_size, baseDecimals),
      makerFeeBps: row.maker_fee_bps,
      takerFeeBps: row.taker_fee_bps,
      feeAsset: row.fee_asset,
      baseDecimals,
      quoteDecimals: row.quote_decimals == null ? undefined : Number(row.quote_decimals),
      feeDecimals: row.fee_decimals == null ? undefined : Number(row.fee_decimals),
      active: row.active
    };
  }

  async getBySymbol(symbol: string): Promise<MarketConfig | null> {
    const result = await this.client.query(
      `SELECT
         m.*,
         base_asset.decimals AS base_decimals,
         quote_asset.decimals AS quote_decimals,
         fee_asset.decimals AS fee_decimals
       FROM markets m
       LEFT JOIN assets base_asset ON UPPER(base_asset.symbol) = UPPER(m.base_asset)
       LEFT JOIN assets quote_asset ON UPPER(quote_asset.symbol) = UPPER(m.quote_asset)
       LEFT JOIN assets fee_asset ON UPPER(fee_asset.symbol) = UPPER(m.fee_asset)
       WHERE m.symbol = $1`,
      [symbol]
    );

    if (result.rows.length === 0) return null;

    const row = result.rows[0];
    const baseDecimals = row.base_decimals == null ? 8 : Number(row.base_decimals);
    return {
      id: row.id,
      symbol: row.symbol,
      baseAsset: row.base_asset,
      quoteAsset: row.quote_asset,
      priceTick: decimalToAtoms(row.price_tick, 8),
      sizeStep: decimalToAtoms(row.size_step, baseDecimals),
      minOrderSize: decimalToAtoms(row.min_order_size, baseDecimals),
      makerFeeBps: row.maker_fee_bps,
      takerFeeBps: row.taker_fee_bps,
      feeAsset: row.fee_asset,
      baseDecimals,
      quoteDecimals: row.quote_decimals == null ? undefined : Number(row.quote_decimals),
      feeDecimals: row.fee_decimals == null ? undefined : Number(row.fee_decimals),
      active: row.active
    };
  }
}
