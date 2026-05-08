/**
 * Repository for trades
 */

import type { PoolClient } from "pg";
import { atomsToDecimal } from "../../engine/deterministic.js";
import type { Trade } from "../../engine/types.js";

const FALLBACK_ASSET_DECIMALS: Record<string, number> = {
  ANM: 9,
  BTC: 8,
  BNB: 18,
  LTC: 8,
  DOGE: 8,
  ZEC: 8,
  DASH: 8,
  BCH: 8,
  USDT: 18,
  USDC: 6,
  ETH: 18,
  SOL: 9
};

function ensureTradeId(value?: string | null): string {
  const v = (value ?? "").trim();
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(v)) {
    return v;
  }
  const rnd = (): string => Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, "0");
  const a = rnd();
  const b = rnd();
  const c = rnd();
  const d = rnd();
  return `${a.slice(0,8)}-${b.slice(0,4)}-4${b.slice(5,8)}-${((parseInt(c.slice(0,2),16)&0x3f)|0x80).toString(16).padStart(2,"0")}${c.slice(2,4)}-${c.slice(4,8)}${d}`;
}

export class TradesRepo {
  private assetDecimals = new Map<string, number>();
  private marketAssets = new Map<string, { baseAsset: string; quoteAsset: string }>();

  constructor(private client: PoolClient) {}

  async insertTrade(trade: Trade): Promise<void> {
    const marketAssets = await this.getMarketAssets((trade as any).marketId);
    const baseDecimals = await this.getAssetDecimals(marketAssets.baseAsset);
    const quoteDecimals = await this.getAssetDecimals(marketAssets.quoteAsset);
    const feeDecimals = await this.getAssetDecimals((trade as any).feeAsset);
    const price = atomsToDecimal(trade.priceAtoms, 8);
    const size = atomsToDecimal(trade.sizeAtoms, baseDecimals);
    const quoteAmount = atomsToDecimal(trade.quoteAmountAtoms, quoteDecimals);
    const makerFee = atomsToDecimal(trade.makerFeeAtoms, feeDecimals);
    const takerFee = atomsToDecimal(trade.takerFeeAtoms, feeDecimals);

    await this.client.query(
      `INSERT INTO trades (
        id, market_id, maker_order_id, taker_order_id,
        price, size, quote_amount, maker_fee, taker_fee,
        fee_asset, fee_bps_maker, fee_bps_taker, sequence, created_at
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)`,
      [
        ensureTradeId((trade as any).id),
        (trade as any).marketId,
        (trade as any).makerOrderId,
        (trade as any).takerOrderId,
        price,
        size,
        quoteAmount,
        makerFee,
        takerFee,
        (trade as any).feeAsset,
        (trade as any).feeBpsMaker,
        (trade as any).feeBpsTaker,
        String((trade as any).sequence),
        (trade as any).createdAt,
      ]
    );
  }

  async insertTrades(trades: Trade[]): Promise<void> {
    for (const trade of trades) {
      await this.insertTrade(trade);
    }
  }

  private async getMarketAssets(marketId: string): Promise<{ baseAsset: string; quoteAsset: string }> {
    const cached = this.marketAssets.get(marketId);
    if (cached) return cached;

    const result = await this.client.query(
      `SELECT base_asset, quote_asset FROM markets WHERE id = $1`,
      [marketId]
    );
    const assets = {
      baseAsset: String(result.rows[0]?.base_asset ?? "BTC").toUpperCase(),
      quoteAsset: String(result.rows[0]?.quote_asset ?? "USDT").toUpperCase(),
    };
    this.marketAssets.set(marketId, assets);
    return assets;
  }

  private async getAssetDecimals(asset: string): Promise<number> {
    const normalized = String(asset || "").toUpperCase();
    const cached = this.assetDecimals.get(normalized);
    if (cached !== undefined) return cached;

    const result = await this.client.query(
      `SELECT decimals FROM assets WHERE UPPER(symbol) = $1 LIMIT 1`,
      [normalized]
    );
    const decimals =
      result.rows[0]?.decimals == null
        ? (FALLBACK_ASSET_DECIMALS[normalized] ?? 8)
        : Number(result.rows[0].decimals);

    this.assetDecimals.set(normalized, decimals);
    return decimals;
  }
}
