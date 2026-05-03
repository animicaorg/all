/**
 * Market Routes
 * Exchange market state and operational controls.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { columnExists, countSql, pagination, rowsSql, tableExists, toInt } from './db_helpers.js';

const marketsQuerySchema = z.object({
  query: z.string().optional(),
  status: z.enum(['ONLINE', 'HALTED', 'READONLY']).optional(),
  ...commonSchemas.paginationQuery.shape,
});

const marketStatusSchema = z.object({
  status: z.enum(['ONLINE', 'HALTED', 'READONLY']),
  reason: z.string().min(3).max(1000).optional(),
});

const positiveDecimalString = z
  .string()
  .trim()
  .regex(/^\d+(\.\d+)?$/)
  .refine((value) => Number(value) > 0, 'Must be greater than 0');

const assetSymbolSchema = z
  .string()
  .trim()
  .min(2)
  .max(20)
  .regex(/^[a-z0-9]+$/i);

const marketCreateSchema = z.object({
  symbol: z
    .string()
    .trim()
    .min(3)
    .max(32)
    .regex(/^[a-z0-9]+-[a-z0-9]+$/i)
    .optional(),
  baseAsset: assetSymbolSchema,
  quoteAsset: assetSymbolSchema,
  priceTick: positiveDecimalString.default('0.01'),
  sizeStep: positiveDecimalString.default('0.001'),
  minOrderSize: positiveDecimalString.default('0.001'),
  makerFeeBps: z.coerce.number().int().min(0).max(10000).default(10),
  takerFeeBps: z.coerce.number().int().min(0).max(10000).default(20),
  feeAsset: assetSymbolSchema.optional(),
  status: z.enum(['ONLINE', 'HALTED', 'READONLY']).default('ONLINE'),
});

const controlsSchema = z.object({
  tradingEnabled: z.boolean(),
  depositsEnabled: z.boolean(),
  withdrawalsEnabled: z.boolean(),
  reason: z.string().max(1000).optional().nullable(),
});

interface MarketRow {
  id: string;
  symbol: string;
  base_asset: string | null;
  quote_asset: string | null;
  active: boolean | null;
  created_at: Date;
  price_tick: string | null;
  size_step: string | null;
  min_order_size: string | null;
  maker_fee_bps: number | null;
  taker_fee_bps: number | null;
  fee_asset: string | null;
}

type MarketAssetSource = 'BITGO' | 'BITCOIN_NODE' | 'ANIMICA_NODE' | 'DB';

interface MarketAssetOption {
  symbol: string;
  name: string;
  decimals: number;
  sources: MarketAssetSource[];
  networks: string[];
  enabled: boolean;
}

interface AssetOptionInput {
  symbol: string;
  name?: string | null;
  decimals?: number | null;
  sources: MarketAssetSource[];
  networks?: Array<string | null | undefined>;
  enabled?: boolean | null;
}

interface AssetOptionRow {
  symbol: string | null;
  name: string | null;
  decimals: number | null;
  active: boolean | null;
  network_code: string | null;
  network_kind: string | null;
  bitgo_coin: string | null;
}

interface BitgoConfigRow {
  wallets: unknown;
  coins: unknown;
  enabled: boolean | null;
}

const ASSET_NAMES: Record<string, string> = {
  ANM: 'Animica',
  BTC: 'Bitcoin',
  ETH: 'Ethereum',
  SOL: 'Solana',
  USDT: 'Tether USD',
  USDC: 'USD Coin',
  LTC: 'Litecoin',
  DOGE: 'Dogecoin',
  BCH: 'Bitcoin Cash',
  DASH: 'Dash',
  ZEC: 'Zcash',
};

const ASSET_DECIMALS: Record<string, number> = {
  ANM: 9,
  BTC: 8,
  ETH: 18,
  SOL: 9,
  USDT: 6,
  USDC: 6,
  LTC: 8,
  DOGE: 8,
  BCH: 8,
  DASH: 8,
  ZEC: 8,
};

const FALLBACK_MARKET_ASSETS: AssetOptionInput[] = [
  { symbol: 'ANM', sources: ['ANIMICA_NODE'], networks: ['ANIMICA'], decimals: ASSET_DECIMALS.ANM },
  { symbol: 'BTC', sources: ['BITGO'], networks: ['BTC'], decimals: ASSET_DECIMALS.BTC },
  { symbol: 'ETH', sources: ['BITGO'], networks: ['ETH'], decimals: ASSET_DECIMALS.ETH },
  { symbol: 'SOL', sources: ['BITGO'], networks: ['SOL'], decimals: ASSET_DECIMALS.SOL },
];

const BITCOIN_STYLE_ENV_PREFIXES: Record<string, string> = {
  BTC: 'BTC',
  BITCOIN: 'BTC',
  LTC: 'LTC',
  LITECOIN: 'LTC',
  DOGE: 'DOGE',
  DOGECOIN: 'DOGE',
  BCH: 'BCH',
  BITCOINCASH: 'BCH',
  DASH: 'DASH',
  ZEC: 'ZEC',
  ZCASH: 'ZEC',
};

function mapAsset(symbol: string | null, createdAt: Date) {
  const safeSymbol = symbol || 'UNKNOWN';
  return {
    id: safeSymbol,
    symbol: safeSymbol,
    name: safeSymbol,
    decimals: 0,
    kind: 'CRYPTO',
    isEnabled: true,
    createdAt,
  };
}

function mapMarket(row: MarketRow) {
  const active = row.active ?? true;
  return {
    id: row.id,
    symbol: row.symbol,
    status: active ? 'ONLINE' : 'HALTED',
    priceTick: row.price_tick ?? '0',
    sizeStep: row.size_step ?? '0',
    minOrderSize: row.min_order_size ?? '0',
    makerFeeBps: row.maker_fee_bps ?? 0,
    takerFeeBps: row.taker_fee_bps ?? 0,
    feeAsset: row.fee_asset ?? row.quote_asset ?? 'USDT',
    createdAt: row.created_at,
    baseAsset: mapAsset(row.base_asset, row.created_at),
    quoteAsset: mapAsset(row.quote_asset, row.created_at),
    marketControl: null,
    _count: {
      orders: 0,
      trades: 0,
    },
  };
}

async function findMarketById(prisma: PrismaClient, id: string): Promise<MarketRow | null> {
  const rows = await rowsSql<MarketRow>(
    prisma,
    `SELECT
      id::text AS id,
      symbol::text AS symbol,
      base_asset::text AS base_asset,
      quote_asset::text AS quote_asset,
      active,
      created_at,
      price_tick::text AS price_tick,
      size_step::text AS size_step,
      min_order_size::text AS min_order_size,
      maker_fee_bps,
      taker_fee_bps,
      fee_asset::text AS fee_asset
    FROM markets
    WHERE id = $1::uuid
    LIMIT 1`,
    id
  );
  return rows[0] ?? null;
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase();
}

function normalizeOptionalSymbol(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  const symbol = value.trim().toUpperCase().replace(/[^A-Z0-9]/g, '');
  return symbol.length > 0 ? symbol : null;
}

function normalizeBitgoCoin(value: unknown): string | null {
  if (typeof value !== 'string') return null;

  const raw = value.trim().toLowerCase();
  if (!raw) return null;

  const explicit: Record<string, string> = {
    btc: 'BTC',
    tbtc: 'BTC',
    eth: 'ETH',
    teth: 'ETH',
    gteth: 'ETH',
    sol: 'SOL',
    tsol: 'SOL',
  };
  if (explicit[raw]) return explicit[raw];

  const lastSegment = raw.includes(':') ? raw.split(':').filter(Boolean).at(-1) : raw;
  const cleaned = lastSegment?.toUpperCase().replace(/[^A-Z0-9]/g, '') ?? '';
  if (!cleaned) return null;
  if (explicit[cleaned.toLowerCase()]) return explicit[cleaned.toLowerCase()];
  return cleaned;
}

function sourceForAssetRow(row: AssetOptionRow): MarketAssetSource {
  const symbol = normalizeOptionalSymbol(row.symbol);
  const networkCode = normalizeOptionalSymbol(row.network_code);
  const networkKind = normalizeOptionalSymbol(row.network_kind);

  if (row.bitgo_coin) return 'BITGO';
  if (symbol === 'ANM' || networkCode === 'ANIMICA' || networkKind === 'ANIMICA') return 'ANIMICA_NODE';
  if (
    networkKind === 'UTXO' ||
    (networkCode !== null && Object.values(BITCOIN_STYLE_ENV_PREFIXES).includes(networkCode))
  ) {
    return 'BITCOIN_NODE';
  }
  return 'DB';
}

function upsertAssetOption(assets: Map<string, MarketAssetOption>, input: AssetOptionInput) {
  const symbol = normalizeOptionalSymbol(input.symbol);
  if (!symbol) return;

  const existing = assets.get(symbol);
  const name = input.name?.trim() || ASSET_NAMES[symbol] || symbol;
  const decimals = input.decimals ?? ASSET_DECIMALS[symbol] ?? 8;
  const enabled = input.enabled ?? true;
  const networks = (input.networks ?? []).map(normalizeOptionalSymbol).filter((network): network is string => Boolean(network));

  if (!existing) {
    assets.set(symbol, {
      symbol,
      name,
      decimals,
      sources: [...new Set(input.sources)].sort(),
      networks: [...new Set(networks)].sort(),
      enabled,
    });
    return;
  }

  for (const source of input.sources) {
    if (!existing.sources.includes(source)) existing.sources.push(source);
  }
  for (const network of networks) {
    if (!existing.networks.includes(network)) existing.networks.push(network);
  }
  existing.sources.sort();
  existing.networks.sort();
  existing.enabled = existing.enabled || enabled;
  if (existing.name === existing.symbol && name !== symbol) existing.name = name;
  if (existing.decimals === 0 && decimals > 0) existing.decimals = decimals;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as Record<string, unknown>;
}

function collectSymbolsFromBitgoConfig(value: unknown, symbols: Set<string>, includeStringValues: boolean) {
  if (typeof value === 'string') {
    if (!includeStringValues) return;
    const symbol = normalizeBitgoCoin(value);
    if (symbol) symbols.add(symbol);
    return;
  }

  if (Array.isArray(value)) {
    for (const entry of value) collectSymbolsFromBitgoConfig(entry, symbols, includeStringValues);
    return;
  }

  const record = asRecord(value);
  if (!record) return;

  for (const key of Object.keys(record)) {
    const symbol = normalizeBitgoCoin(key);
    if (symbol) symbols.add(symbol);
  }

  for (const key of ['symbol', 'asset', 'assetId', 'code', 'coin', 'bitgoCoin', 'chain', 'network']) {
    const symbol = normalizeBitgoCoin(record[key]);
    if (symbol) symbols.add(symbol);
  }
}

async function addDatabaseAssets(prisma: PrismaClient, assets: Map<string, MarketAssetOption>) {
  if (!(await tableExists(prisma, 'assets'))) return;

  const [hasAssetNetworks, hasNetworks, hasActive, hasIsEnabled, hasBitgoCoin, hasNetworkType, hasNetworkKind] =
    await Promise.all([
      tableExists(prisma, 'asset_networks'),
      tableExists(prisma, 'networks'),
      columnExists(prisma, 'assets', 'active'),
      columnExists(prisma, 'assets', 'is_enabled'),
      columnExists(prisma, 'asset_networks', 'bitgo_coin'),
      columnExists(prisma, 'networks', 'type'),
      columnExists(prisma, 'networks', 'kind'),
    ]);

  const enabledExpr = hasActive ? 'assets.active' : hasIsEnabled ? 'assets.is_enabled' : 'true';
  const bitgoCoinExpr = hasAssetNetworks && hasBitgoCoin ? 'asset_networks.bitgo_coin::text' : 'NULL::text';
  const networkCodeExpr = hasAssetNetworks && hasNetworks ? 'networks.code::text' : 'NULL::text';
  const networkKindExpr =
    hasAssetNetworks && hasNetworks && hasNetworkType
      ? 'networks.type::text'
      : hasAssetNetworks && hasNetworks && hasNetworkKind
        ? 'networks.kind::text'
        : 'NULL::text';
  const joins = [
    hasAssetNetworks ? 'LEFT JOIN asset_networks ON asset_networks.asset_id = assets.id' : '',
    hasAssetNetworks && hasNetworks ? 'LEFT JOIN networks ON networks.id = asset_networks.network_id' : '',
  ]
    .filter(Boolean)
    .join('\n');

  const rows = await rowsSql<AssetOptionRow>(
    prisma,
    `SELECT
      assets.symbol::text AS symbol,
      assets.name::text AS name,
      assets.decimals AS decimals,
      ${enabledExpr} AS active,
      ${networkCodeExpr} AS network_code,
      ${networkKindExpr} AS network_kind,
      ${bitgoCoinExpr} AS bitgo_coin
    FROM assets
    ${joins}
    ORDER BY assets.symbol ASC`
  );

  for (const row of rows) {
    if (!row.symbol) continue;

    const source = sourceForAssetRow(row);
    upsertAssetOption(assets, {
      symbol: row.symbol,
      name: row.name,
      decimals: row.decimals,
      enabled: row.active ?? true,
      sources: [source],
      networks: [row.network_code],
    });

    const bitgoSymbol = normalizeBitgoCoin(row.bitgo_coin);
    if (bitgoSymbol) {
      upsertAssetOption(assets, {
        symbol: bitgoSymbol,
        name: row.name,
        decimals: row.decimals,
        enabled: row.active ?? true,
        sources: ['BITGO'],
        networks: [row.network_code],
      });
    }
  }
}

async function addBitgoConfiguredAssets(prisma: PrismaClient, assets: Map<string, MarketAssetOption>) {
  if (!(await tableExists(prisma, 'bitgo_configs'))) return;

  const rows = await rowsSql<BitgoConfigRow>(
    prisma,
    `SELECT wallets, coins, enabled
    FROM bitgo_configs
    ORDER BY updated_at DESC NULLS LAST
    LIMIT 1`
  );
  const config = rows[0];
  if (!config?.enabled) return;

  const symbols = new Set<string>();
  collectSymbolsFromBitgoConfig(config.coins, symbols, true);
  collectSymbolsFromBitgoConfig(config.wallets, symbols, false);

  for (const symbol of symbols) {
    upsertAssetOption(assets, {
      symbol,
      sources: ['BITGO'],
      networks: [symbol],
      decimals: ASSET_DECIMALS[symbol],
    });
  }
}

function addNodeConfiguredAssets(config: Config, assets: Map<string, MarketAssetOption>) {
  if (config.ANIMICA_NODE_URL || process.env.ANIMICA_RPC_URL) {
    upsertAssetOption(assets, {
      symbol: 'ANM',
      sources: ['ANIMICA_NODE'],
      networks: ['ANIMICA'],
      decimals: ASSET_DECIMALS.ANM,
    });
  }

  for (const [key, value] of Object.entries(process.env)) {
    if (!value) continue;

    const normalizedKey = key.toUpperCase().replace(/[^A-Z0-9_]/g, '');
    if (!/(RPC|NODE|DAEMON)/.test(normalizedKey)) continue;
    if (!/(URL|HOST|PORT|USER|PASSWORD|COOKIE|WALLET|NETWORK)/.test(normalizedKey)) continue;

    for (const [prefix, symbol] of Object.entries(BITCOIN_STYLE_ENV_PREFIXES)) {
      if (!normalizedKey.startsWith(`${prefix}_`)) continue;
      upsertAssetOption(assets, {
        symbol,
        sources: ['BITCOIN_NODE'],
        networks: [symbol],
        decimals: ASSET_DECIMALS[symbol],
      });
    }
  }
}

async function listMarketAssetOptions(prisma: PrismaClient, config: Config): Promise<MarketAssetOption[]> {
  const assets = new Map<string, MarketAssetOption>();

  await addDatabaseAssets(prisma, assets);
  await addBitgoConfiguredAssets(prisma, assets);
  addNodeConfiguredAssets(config, assets);

  for (const asset of FALLBACK_MARKET_ASSETS) {
    if (!assets.has(asset.symbol)) upsertAssetOption(assets, asset);
  }

  return [...assets.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
}

function statusToActive(status: 'ONLINE' | 'HALTED' | 'READONLY'): boolean {
  return status === 'ONLINE';
}

export function createMarketsRouter(
  prisma: PrismaClient,
  config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.MARKETS_READ),
    validateQuery(marketsQuerySchema),
    async (req, res, next) => {
      try {
        const { query, status, page = 1, limit = 50 } = req.query as any;
        if (!(await tableExists(prisma, 'markets'))) {
          res.json({
            success: true,
            data: {
              markets: [],
              pagination: pagination(page, limit, 0),
            },
          });
          return;
        }

        const where: string[] = [];
        const values: unknown[] = [];
        if (query) {
          where.push(`symbol ILIKE $${values.length + 1}`);
          values.push(`%${query}%`);
        }
        if (status) {
          if (status === 'ONLINE') {
            where.push('active = true');
          } else if (status === 'HALTED') {
            where.push('active = false');
          } else {
            where.push('false');
          }
        }

        const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
        const total = await countSql(prisma, `SELECT COUNT(*)::bigint AS count FROM markets ${whereSql}`, ...values);
        const markets = await rowsSql<MarketRow>(
          prisma,
          `SELECT
            id::text AS id,
            symbol::text AS symbol,
            base_asset::text AS base_asset,
            quote_asset::text AS quote_asset,
            active,
            created_at,
            price_tick::text AS price_tick,
            size_step::text AS size_step,
            min_order_size::text AS min_order_size,
            maker_fee_bps,
            taker_fee_bps,
            fee_asset::text AS fee_asset
          FROM markets
          ${whereSql}
          ORDER BY symbol ASC
          OFFSET $${values.length + 1}
          LIMIT $${values.length + 2}`,
          ...values,
          (page - 1) * limit,
          limit
        );

        res.json({
          success: true,
          data: {
            markets: markets.map(mapMarket),
            pagination: pagination(page, limit, total),
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    '/assets',
    requirePermission(PERMISSIONS.MARKETS_READ),
    async (_req, res, next) => {
      try {
        const assets = await listMarketAssetOptions(prisma, config);
        res.json({ success: true, data: { assets } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/',
    requirePermission(PERMISSIONS.MARKETS_WRITE),
    validateBody(marketCreateSchema),
    async (req, res, next) => {
      try {
        if (!(await tableExists(prisma, 'markets'))) {
          res.status(503).json({ error: 'Unavailable', message: 'Markets table is not available' });
          return;
        }

        const baseAsset = normalizeSymbol(req.body.baseAsset);
        const quoteAsset = normalizeSymbol(req.body.quoteAsset);
        const symbol = normalizeSymbol(req.body.symbol ?? `${baseAsset}-${quoteAsset}`);
        const feeAsset = normalizeSymbol(req.body.feeAsset ?? quoteAsset);

        if (baseAsset === quoteAsset) {
          res.status(400).json({ error: 'ValidationError', message: 'Base and quote assets must differ' });
          return;
        }

        const existing = await rowsSql<{ id: string }>(
          prisma,
          'SELECT id::text AS id FROM markets WHERE symbol = $1 LIMIT 1',
          symbol
        );
        if (existing.length > 0) {
          res.status(409).json({ error: 'Conflict', message: `Market ${symbol} already exists` });
          return;
        }

        const rows = await prisma.$queryRawUnsafe<MarketRow[]>(
          `INSERT INTO markets (
            symbol,
            base_asset,
            quote_asset,
            price_tick,
            size_step,
            min_order_size,
            maker_fee_bps,
            taker_fee_bps,
            fee_asset,
            active
          )
          VALUES ($1, $2, $3, $4::numeric, $5::numeric, $6::numeric, $7, $8, $9, $10)
          RETURNING
            id::text AS id,
            symbol::text AS symbol,
            base_asset::text AS base_asset,
            quote_asset::text AS quote_asset,
            active,
            created_at,
            price_tick::text AS price_tick,
            size_step::text AS size_step,
            min_order_size::text AS min_order_size,
            maker_fee_bps,
            taker_fee_bps,
            fee_asset::text AS fee_asset`,
          symbol,
          baseAsset,
          quoteAsset,
          req.body.priceTick,
          req.body.sizeStep,
          req.body.minOrderSize,
          req.body.makerFeeBps,
          req.body.takerFeeBps,
          feeAsset,
          statusToActive(req.body.status)
        );

        const market = rows[0];
        if (!market) {
          throw new Error('Market insert did not return a row');
        }
        if (market && (await tableExists(prisma, 'market_sequence'))) {
          await prisma.$executeRawUnsafe(
            `INSERT INTO market_sequence (market_id, last_seq)
             VALUES ($1::uuid, 0)
             ON CONFLICT (market_id) DO NOTHING`,
            market.id
          );
        }

        await req.auditLog?.({
          action: 'CREATE_MARKET',
          entityType: 'MARKET',
          entityId: market.id,
          afterSnapshot: mapMarket(market),
        });

        res.status(201).json({ success: true, data: { market: mapMarket(market) } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id/status',
    requirePermission(PERMISSIONS.MARKETS_HALT),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(marketStatusSchema),
    async (req, res, next) => {
      try {
        const existing = await findMarketById(prisma, req.params.id);

        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        await prisma.$executeRawUnsafe(
          'UPDATE markets SET active = $1 WHERE id = $2::uuid',
          statusToActive(req.body.status),
          req.params.id
        );
        const updated = await findMarketById(prisma, req.params.id);

        await req.auditLog?.({
          action: req.body.status === 'ONLINE' ? 'RESUME_MARKET' : 'HALT_MARKET',
          entityType: 'MARKET',
          entityId: req.params.id,
          beforeSnapshot: mapMarket(existing),
          afterSnapshot: { status: req.body.status },
          metadata: { reason: req.body.reason },
        });

        res.json({ success: true, data: { market: mapMarket(updated ?? existing) } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.put(
    '/:id/controls',
    requirePermission(PERMISSIONS.MARKETS_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(controlsSchema),
    async (req, res, next) => {
      try {
        const market = await findMarketById(prisma, req.params.id);

        if (!market) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        await prisma.$executeRawUnsafe(
          'UPDATE markets SET active = $1 WHERE id = $2::uuid',
          req.body.tradingEnabled,
          req.params.id
        );

        const control = {
          id: req.params.id,
          marketId: req.params.id,
          tradingEnabled: req.body.tradingEnabled,
          depositsEnabled: req.body.depositsEnabled,
          withdrawalsEnabled: req.body.withdrawalsEnabled,
          reason: req.body.reason ?? null,
          updatedBy: req.admin!.id,
          updatedAt: new Date(),
        };

        await req.auditLog?.({
          action: 'UPDATE_MARKET_CONTROLS',
          entityType: 'MARKET',
          entityId: req.params.id,
          beforeSnapshot: mapMarket(market),
          afterSnapshot: control,
        });

        res.json({ success: true, data: { control } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.post(
    '/:id/cancel-open-orders',
    requirePermission(PERMISSIONS.MARKETS_HALT),
    validateParams(z.object({ id: commonSchemas.uuid })),
    async (req, res, next) => {
      try {
        const market = await findMarketById(prisma, req.params.id);
        if (!market) {
          res.status(404).json({ error: 'NotFound', message: 'Market not found' });
          return;
        }

        const [result] = await rowsSql<{ count: bigint | number | string }>(
          prisma,
          `WITH updated AS (
            UPDATE orders
            SET status = 'CANCELED',
                completed_at = NOW()
            WHERE market_id = $1::uuid
              AND status IN ('ACCEPTED', 'PARTIAL_FILL', 'OPEN', 'PARTIALLY_FILLED')
            RETURNING 1
          )
          SELECT COUNT(*)::bigint AS count FROM updated`,
          req.params.id
        );
        const canceledOrders = toInt(result?.count);

        await req.auditLog?.({
          action: 'CANCEL_ALL_ORDERS',
          entityType: 'MARKET',
          entityId: req.params.id,
          metadata: { canceledOrders },
        });

        res.json({ success: true, data: { canceledOrders } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
