/**
 * Wallet Routes
 * Asset network, wallet provider, and transfer rail controls.
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Config } from '../../config.js';
import type { Logger } from '../../utils/logger.js';
import { validateBody, validateParams, validateQuery, commonSchemas } from '../middleware/validation.js';
import { requirePermission, PERMISSIONS } from '../middleware/rbac.js';
import { countSql, pagination, rowsSql, tableExists } from './db_helpers.js';

const walletProviderValues = ['BITGO', 'ANIMICA_NODE', 'BITCOIN_NODE', 'LOCAL_ANIMICA', 'OTHER'] as const;
const configurableWalletProviderValues = ['BITGO', 'ANIMICA_NODE', 'BITCOIN_NODE'] as const;

const walletQuerySchema = z.object({
  provider: z.enum(walletProviderValues).optional(),
  purpose: z.enum(['HOT', 'WARM', 'COLD', 'TREASURY', 'FEE']).optional(),
  active: z.coerce.boolean().optional(),
  ...commonSchemas.paginationQuery.shape,
});

const walletPatchSchema = z.object({
  providerRef: z.string().min(1).max(255).optional(),
  address: z.string().max(255).optional().nullable(),
  isActive: z.boolean().optional(),
});

const assetNetworkPatchSchema = z.object({
  depositEnabled: z.boolean().optional(),
  withdrawalEnabled: z.boolean().optional(),
  minWithdrawal: z.string().regex(/^\d+(\.\d+)?$/).optional(),
  withdrawalFee: z.string().regex(/^\d+(\.\d+)?$/).optional(),
});

const providerSetupSchema = z.object({
  assetNetworkId: commonSchemas.uuid,
  provider: z.enum(configurableWalletProviderValues),
  walletId: z.string().trim().max(255).optional(),
  assetName: z.string().trim().max(120).optional().nullable(),
  address: z.string().trim().max(255).optional().nullable(),
  rpcUrl: z.string().trim().url().optional().nullable(),
  bitgoCoin: z.string().trim().max(80).optional().nullable(),
  depositEnabled: z.boolean().optional(),
  withdrawalEnabled: z.boolean().optional(),
});

interface WalletRow {
  id: string;
  provider: string | null;
  provider_ref: string | null;
  address: string | null;
  asset_network_id: string | null;
  wallet_metadata: Record<string, unknown> | null;
  status: string | null;
  created_at: Date;
  network_id: string | null;
  network_code: string | null;
  network_name: string | null;
  network_kind: string | null;
  confirmations_required: number | null;
  network_metadata: Record<string, unknown> | null;
  network_created_at: Date | null;
}

interface AssetNetworkRow {
  id: string;
  asset_id: string;
  network_id: string;
  contract_address: string | null;
  bitgo_coin: string | null;
  asset_network_metadata: Record<string, unknown> | null;
  deposit_enabled: boolean | null;
  withdrawal_enabled: boolean | null;
  min_withdrawal: string | null;
  withdrawal_fee: string | null;
  created_at: Date;
  asset_symbol: string | null;
  asset_name: string | null;
  asset_decimals: number | null;
  asset_active: boolean | null;
  asset_created_at: Date | null;
  network_code: string | null;
  network_name: string | null;
  network_kind: string | null;
  confirmations_required: number | null;
  network_metadata: Record<string, unknown> | null;
  network_created_at: Date | null;
}

interface AssetRow {
  id: string;
  symbol: string;
  name: string | null;
  decimals: number | null;
  active: boolean | null;
  created_at: Date;
}

interface NetworkRow {
  id: string;
  code: string;
  name: string | null;
  kind: string | null;
  confirmations_required: number | null;
  metadata?: Record<string, unknown> | null;
  created_at: Date;
}

function metadataRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function mapAsset(row: Pick<AssetRow, 'id' | 'symbol' | 'name' | 'decimals' | 'active' | 'created_at'>) {
  return {
    id: row.id,
    symbol: row.symbol,
    name: row.name ?? row.symbol,
    decimals: row.decimals ?? 0,
    kind: 'CRYPTO',
    isEnabled: row.active ?? true,
    createdAt: row.created_at,
  };
}

function mapNetwork(
  row: Pick<NetworkRow, 'id' | 'code' | 'name' | 'kind' | 'confirmations_required' | 'created_at'> & {
    metadata?: Record<string, unknown> | null;
  }
) {
  const metadata = metadataRecord(row.metadata);
  return {
    id: row.id,
    code: row.code,
    kind: row.kind ?? row.name ?? row.code,
    chainId: typeof metadata.chain_id === 'number' || typeof metadata.chain_id === 'string' ? String(metadata.chain_id) : null,
    rpcUrl: typeof metadata.rpc_url === 'string' ? metadata.rpc_url : null,
    confirmationsRequired: row.confirmations_required ?? 0,
    createdAt: row.created_at,
  };
}

function normalizeProvider(provider: string | null | undefined): string {
  if (provider === 'LOCAL_ANIMICA') return 'ANIMICA_NODE';
  return provider ?? 'OTHER';
}

function inferAssetNetworkProvider(row: AssetNetworkRow, network: ReturnType<typeof mapNetwork>): string {
  const metadata = metadataRecord(row.asset_network_metadata);
  const metadataProvider = typeof metadata.provider === 'string' ? metadata.provider : null;
  if (metadataProvider) return normalizeProvider(metadataProvider);
  if (row.bitgo_coin) return 'BITGO';
  if (row.asset_symbol === 'ANM' || network.code === 'ANIMICA' || network.kind === 'ANIMICA' || network.kind === 'ACCOUNT') {
    return 'ANIMICA_NODE';
  }
  if (network.kind === 'UTXO' || network.code === 'BTC') return 'BITCOIN_NODE';
  return 'OTHER';
}

function mapAssetNetwork(row: AssetNetworkRow) {
  const asset = mapAsset({
    id: row.asset_id,
    symbol: row.asset_symbol ?? row.asset_id,
    name: row.asset_name ?? row.asset_symbol ?? row.asset_id,
    decimals: row.asset_decimals,
    active: row.asset_active,
    created_at: row.asset_created_at ?? row.created_at,
  });
  const network = mapNetwork({
    id: row.network_id,
    code: row.network_code ?? row.network_id,
    name: row.network_name,
    kind: row.network_kind,
    confirmations_required: row.confirmations_required,
    metadata: row.network_metadata,
    created_at: row.network_created_at ?? row.created_at,
  });

  return {
    id: row.id,
    assetId: row.asset_id,
    networkId: row.network_id,
    contractAddress: row.contract_address,
    bitgoCoin: row.bitgo_coin,
    provider: inferAssetNetworkProvider(row, network),
    rpcUrl:
      typeof metadataRecord(row.asset_network_metadata).rpc_url === 'string'
        ? (metadataRecord(row.asset_network_metadata).rpc_url as string)
        : network.rpcUrl,
    depositEnabled: row.deposit_enabled ?? true,
    withdrawalEnabled: row.withdrawal_enabled ?? true,
    minWithdrawal: row.min_withdrawal ?? '0',
    withdrawalFee: row.withdrawal_fee ?? '0',
    asset,
    network,
    _count: {
      depositAddresses: 0,
      deposits: 0,
      withdrawals: 0,
    },
  };
}

function mapWallet(row: WalletRow) {
  const metadata = metadataRecord(row.wallet_metadata);
  const purpose = typeof metadata.purpose === 'string' ? metadata.purpose : 'HOT';
  return {
    id: row.id,
    networkId: row.network_id ?? '',
    assetNetworkId: row.asset_network_id ?? '',
    purpose,
    provider: normalizeProvider(row.provider),
    providerRef: row.provider_ref ?? row.id,
    address: row.address,
    isActive: row.status ? !['disabled', 'inactive', 'closed'].includes(row.status.toLowerCase()) : true,
    createdAt: row.created_at,
    network: mapNetwork({
      id: row.network_id ?? '',
      code: row.network_code ?? 'UNKNOWN',
      name: row.network_name,
      kind: row.network_kind,
      confirmations_required: row.confirmations_required,
      metadata: row.network_metadata,
      created_at: row.network_created_at ?? row.created_at,
    }),
    _count: {
      assignedAddresses: 0,
    },
  };
}

function defaultNodeWalletId(provider: 'ANIMICA_NODE' | 'BITCOIN_NODE', row: AssetNetworkRow): string {
  const asset = row.asset_symbol ?? row.asset_id;
  const network = row.network_code ?? row.network_id;
  const prefix = provider === 'ANIMICA_NODE' ? 'animica-node' : 'bitcoin-node';
  return `${prefix}:${network}:${asset}`;
}

export function createWalletsRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  const getAssetNetworkRow = async (id: string): Promise<AssetNetworkRow | null> => {
    const hasPolicies = await tableExists(prisma, 'withdrawal_policies');
    const policySelect = hasPolicies
      ? `COALESCE(withdrawal_policies.min_withdrawal_atoms::text, asset_networks.min_deposit_atoms::text, '0') AS min_withdrawal,
         COALESCE(withdrawal_policies.metadata->>'withdrawalFeeAtoms', '0') AS withdrawal_fee`
      : `COALESCE(asset_networks.min_deposit_atoms::text, '0') AS min_withdrawal,
         '0'::text AS withdrawal_fee`;
    const policyJoin = hasPolicies
      ? 'LEFT JOIN withdrawal_policies ON withdrawal_policies.asset_network_id = asset_networks.id'
      : '';

    const rows = await rowsSql<AssetNetworkRow>(
      prisma,
      `SELECT
        asset_networks.id::text AS id,
        asset_networks.asset_id::text AS asset_id,
        asset_networks.network_id::text AS network_id,
        asset_networks.contract_address::text AS contract_address,
        asset_networks.bitgo_coin::text AS bitgo_coin,
        asset_networks.metadata AS asset_network_metadata,
        asset_networks.deposits_enabled AS deposit_enabled,
        asset_networks.withdrawals_enabled AS withdrawal_enabled,
        ${policySelect},
        asset_networks.created_at,
        assets.symbol::text AS asset_symbol,
        assets.name::text AS asset_name,
        assets.decimals AS asset_decimals,
        assets.active AS asset_active,
        assets.created_at AS asset_created_at,
        networks.code::text AS network_code,
        networks.name::text AS network_name,
        networks.type::text AS network_kind,
        networks.confirmations_required,
        networks.metadata AS network_metadata,
        networks.created_at AS network_created_at
      FROM asset_networks
      LEFT JOIN assets ON assets.id = asset_networks.asset_id
      LEFT JOIN networks ON networks.id = asset_networks.network_id
      ${policyJoin}
      WHERE asset_networks.id = $1::uuid
      LIMIT 1`,
      id
    );
    return rows[0] ?? null;
  };

  const getWalletRow = async (id: string): Promise<WalletRow | null> => {
    const rows = await rowsSql<WalletRow>(
      prisma,
      `SELECT
        wallets.id::text AS id,
        wallets.provider::text AS provider,
        wallets.wallet_id::text AS provider_ref,
        wallets.metadata->>'address' AS address,
        wallets.asset_network_id::text AS asset_network_id,
        wallets.metadata AS wallet_metadata,
        wallets.status::text AS status,
        wallets.created_at,
        networks.id::text AS network_id,
        networks.code::text AS network_code,
        networks.name::text AS network_name,
        networks.type::text AS network_kind,
        networks.confirmations_required,
        networks.metadata AS network_metadata,
        networks.created_at AS network_created_at
      FROM wallets
      LEFT JOIN asset_networks ON asset_networks.id = wallets.asset_network_id
      LEFT JOIN networks ON networks.id = asset_networks.network_id
      WHERE wallets.id = $1::uuid
      LIMIT 1`,
      id
    );
    return rows[0] ?? null;
  };

  router.put(
    '/provider-setup',
    requirePermission(PERMISSIONS.WALLETS_WRITE),
    validateBody(providerSetupSchema),
    async (req, res, next) => {
      try {
        if (!(await tableExists(prisma, 'wallets'))) {
          res.status(503).json({ error: 'Unavailable', message: 'Wallet infrastructure tables are not initialized.' });
          return;
        }

        const body = req.body as z.infer<typeof providerSetupSchema>;
        const existing = await getAssetNetworkRow(body.assetNetworkId);
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Asset network not found' });
          return;
        }

        const beforeSnapshot = mapAssetNetwork(existing);
        const provider = body.provider;
        const walletId = body.walletId?.trim();
        if (provider === 'BITGO' && !walletId) {
          res.status(400).json({ error: 'BadRequest', message: 'BitGo setup requires a BitGo wallet ID.' });
          return;
        }

        const bitgoCoin =
          provider === 'BITGO'
            ? body.bitgoCoin?.trim() || existing.bitgo_coin || (existing.asset_symbol ?? '').toLowerCase()
            : null;
        const existingRpcUrl =
          typeof metadataRecord(existing.asset_network_metadata).rpc_url === 'string'
            ? (metadataRecord(existing.asset_network_metadata).rpc_url as string)
            : beforeSnapshot.network.rpcUrl;
        const rpcUrl = provider === 'BITGO' ? null : body.rpcUrl?.trim() || existingRpcUrl || null;
        if (provider !== 'BITGO' && !rpcUrl) {
          res.status(400).json({ error: 'BadRequest', message: 'Node setup requires an RPC URL.' });
          return;
        }

        const effectiveWalletId =
          walletId ||
          (provider === 'ANIMICA_NODE' || provider === 'BITCOIN_NODE' ? defaultNodeWalletId(provider, existing) : '');
        const assetName = provider === 'BITCOIN_NODE' ? body.assetName?.trim() || null : null;
        const depositsEnabled = body.depositEnabled ?? existing.deposit_enabled ?? true;
        const withdrawalsEnabled = body.withdrawalEnabled ?? existing.withdrawal_enabled ?? true;
        const address = body.address?.trim() || null;

        const assetNetworkMetadata: Record<string, unknown> = {
          provider,
          configured_via: 'admin-web',
        };
        if (bitgoCoin) assetNetworkMetadata.bitgo_coin = bitgoCoin;
        if (rpcUrl) assetNetworkMetadata.rpc_url = rpcUrl;
        if (assetName) assetNetworkMetadata.asset_name = assetName;

        await prisma.$executeRawUnsafe(
          `UPDATE asset_networks
           SET bitgo_coin = $1,
               deposits_enabled = $2,
               withdrawals_enabled = $3,
               metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb
           WHERE id = $5::uuid`,
          bitgoCoin,
          depositsEnabled,
          withdrawalsEnabled,
          JSON.stringify(assetNetworkMetadata),
          body.assetNetworkId
        );

        if (rpcUrl) {
          await prisma.$executeRawUnsafe(
            `UPDATE networks
             SET metadata = COALESCE(metadata, '{}'::jsonb) || $1::jsonb
             WHERE id = $2::uuid`,
            JSON.stringify({ rpc_url: rpcUrl }),
            existing.network_id
          );
        }

        if (assetName) {
          await prisma.$executeRawUnsafe(
            `UPDATE assets
             SET name = $1
             WHERE id = $2::uuid`,
            assetName,
            existing.asset_id
          );
        }

        const walletMetadata: Record<string, unknown> = {
          purpose: 'HOT',
          provider,
          configured_via: 'admin-web',
          address,
        };
        if (bitgoCoin) walletMetadata.bitgo_coin = bitgoCoin;
        if (rpcUrl) walletMetadata.rpc_url = rpcUrl;
        if (assetName) walletMetadata.asset_name = assetName;

        const insertedRows = await prisma.$queryRawUnsafe<Array<{ id: string }>>(
          `INSERT INTO wallets (provider, wallet_id, asset_network_id, status, metadata)
           VALUES ($1, $2, $3::uuid, 'ACTIVE', $4::jsonb)
           ON CONFLICT (provider, wallet_id, asset_network_id) DO UPDATE SET
             status = 'ACTIVE',
             metadata = COALESCE(wallets.metadata, '{}'::jsonb) || EXCLUDED.metadata,
             updated_at = NOW()
           RETURNING id::text AS id`,
          provider,
          effectiveWalletId,
          body.assetNetworkId,
          JSON.stringify(walletMetadata)
        );

        const [walletRow, assetNetworkRow] = await Promise.all([
          getWalletRow(insertedRows[0].id),
          getAssetNetworkRow(body.assetNetworkId),
        ]);

        const wallet = walletRow ? mapWallet(walletRow) : null;
        const assetNetwork = assetNetworkRow ? mapAssetNetwork(assetNetworkRow) : beforeSnapshot;

        await req.auditLog?.({
          action: 'CONFIGURE_WALLET_PROVIDER',
          entityType: 'ASSET_NETWORK',
          entityId: body.assetNetworkId,
          beforeSnapshot,
          afterSnapshot: { wallet, assetNetwork },
        });

        res.json({ success: true, data: { wallet, assetNetwork } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.get(
    '/',
    requirePermission(PERMISSIONS.WALLETS_READ),
    validateQuery(walletQuerySchema),
    async (req, res, next) => {
      try {
        const { provider, purpose, active, page = 1, limit = 50 } = req.query as any;
        const where: string[] = [];
        const values: unknown[] = [];
        if (provider) {
          where.push(`provider = $${values.length + 1}`);
          values.push(provider);
        }
        if (purpose && purpose !== 'HOT') {
          where.push('false');
        }
        if (typeof active === 'boolean') {
          where.push(
            active
              ? "COALESCE(lower(status) NOT IN ('disabled', 'inactive', 'closed'), true)"
              : "NOT COALESCE(lower(status) NOT IN ('disabled', 'inactive', 'closed'), true)"
          );
        }

        const whereSql = where.length ? `WHERE ${where.join(' AND ')}` : '';
        const walletsExist = await tableExists(prisma, 'wallets');
        const total = walletsExist
          ? await countSql(prisma, `SELECT COUNT(*)::bigint AS count FROM wallets ${whereSql}`, ...values)
          : 0;

        const hasPolicies = await tableExists(prisma, 'withdrawal_policies');
        const policySelect = hasPolicies
          ? `COALESCE(withdrawal_policies.min_withdrawal_atoms::text, asset_networks.min_deposit_atoms::text, '0') AS min_withdrawal,
             COALESCE(withdrawal_policies.metadata->>'withdrawalFeeAtoms', '0') AS withdrawal_fee`
          : `COALESCE(asset_networks.min_deposit_atoms::text, '0') AS min_withdrawal,
             '0'::text AS withdrawal_fee`;
        const policyJoin = hasPolicies
          ? 'LEFT JOIN withdrawal_policies ON withdrawal_policies.asset_network_id = asset_networks.id'
          : '';

        const [walletRows, assetNetworkRows, assetRows, networkRows] = await Promise.all([
          walletsExist
            ? rowsSql<WalletRow>(
                prisma,
                `SELECT
                  wallets.id::text AS id,
                  wallets.provider::text AS provider,
                  wallets.wallet_id::text AS provider_ref,
                  wallets.metadata->>'address' AS address,
                  wallets.asset_network_id::text AS asset_network_id,
                  wallets.metadata AS wallet_metadata,
                  wallets.status::text AS status,
                  wallets.created_at,
                  networks.id::text AS network_id,
                  networks.code::text AS network_code,
                  networks.name::text AS network_name,
                  networks.type::text AS network_kind,
                  networks.confirmations_required,
                  networks.metadata AS network_metadata,
                  networks.created_at AS network_created_at
                FROM wallets
                LEFT JOIN asset_networks ON asset_networks.id = wallets.asset_network_id
                LEFT JOIN networks ON networks.id = asset_networks.network_id
                ${whereSql}
                ORDER BY wallets.provider ASC, wallets.created_at DESC
                OFFSET $${values.length + 1}
                LIMIT $${values.length + 2}`,
                ...values,
                (page - 1) * limit,
                limit
              )
            : Promise.resolve([]),
          tableExists(prisma, 'asset_networks').then((exists) =>
            exists
              ? rowsSql<AssetNetworkRow>(
                  prisma,
                  `SELECT
                    asset_networks.id::text AS id,
                    asset_networks.asset_id::text AS asset_id,
                    asset_networks.network_id::text AS network_id,
                    asset_networks.contract_address::text AS contract_address,
                    asset_networks.bitgo_coin::text AS bitgo_coin,
                    asset_networks.metadata AS asset_network_metadata,
                    asset_networks.deposits_enabled AS deposit_enabled,
                    asset_networks.withdrawals_enabled AS withdrawal_enabled,
                    ${policySelect},
                    asset_networks.created_at,
                    assets.symbol::text AS asset_symbol,
                    assets.name::text AS asset_name,
                    assets.decimals AS asset_decimals,
                    assets.active AS asset_active,
                    assets.created_at AS asset_created_at,
                    networks.code::text AS network_code,
                    networks.name::text AS network_name,
                    networks.type::text AS network_kind,
                    networks.confirmations_required,
                    networks.metadata AS network_metadata,
                    networks.created_at AS network_created_at
                  FROM asset_networks
                  LEFT JOIN assets ON assets.id = asset_networks.asset_id
                  LEFT JOIN networks ON networks.id = asset_networks.network_id
                  ${policyJoin}
                  ORDER BY assets.symbol ASC, networks.code ASC`
                )
              : Promise.resolve([])
          ),
          tableExists(prisma, 'assets').then((exists) =>
            exists
              ? rowsSql<AssetRow>(
                  prisma,
                  `SELECT
                    id::text AS id,
                    symbol::text AS symbol,
                    name::text AS name,
                    decimals,
                    active,
                    created_at
                  FROM assets
                  ORDER BY symbol ASC`
                )
              : Promise.resolve([])
          ),
          tableExists(prisma, 'networks').then((exists) =>
            exists
              ? rowsSql<NetworkRow>(
                  prisma,
                  `SELECT
                    id::text AS id,
                    code::text AS code,
                    name::text AS name,
                    type::text AS kind,
                    confirmations_required,
                    metadata,
                    created_at
                  FROM networks
                  ORDER BY code ASC`
                )
              : Promise.resolve([])
          ),
        ]);

        res.json({
          success: true,
          data: {
            wallets: walletRows.map(mapWallet),
            assetNetworks: assetNetworkRows.map(mapAssetNetwork),
            assets: assetRows.map(mapAsset),
            networks: networkRows.map(mapNetwork),
            pagination: pagination(page, limit, total),
          },
        });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/:id',
    requirePermission(PERMISSIONS.WALLETS_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(walletPatchSchema),
    async (req, res, next) => {
      try {
        const existingRows = await rowsSql<Record<string, unknown>>(
          prisma,
          'SELECT * FROM wallets WHERE id = $1::uuid LIMIT 1',
          req.params.id
        );
        const existing = existingRows[0];
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Wallet not found' });
          return;
        }

        const updates: string[] = [];
        const values: unknown[] = [];
        if (req.body.providerRef !== undefined) {
          values.push(req.body.providerRef);
          updates.push(`wallet_id = $${values.length}`);
        }
        if (req.body.address !== undefined) {
          values.push(req.body.address);
          updates.push(`metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{address}', to_jsonb($${values.length}::text), true)`);
        }
        if (req.body.isActive !== undefined) {
          values.push(req.body.isActive ? 'ACTIVE' : 'PAUSED');
          updates.push(`status = $${values.length}`);
        }

        if (updates.length > 0) {
          values.push(req.params.id);
          await prisma.$executeRawUnsafe(
            `UPDATE wallets SET ${updates.join(', ')}, updated_at = NOW() WHERE id = $${values.length}::uuid`,
            ...values
          );
        }

        const walletRows = await rowsSql<WalletRow>(
          prisma,
          `SELECT
            wallets.id::text AS id,
            wallets.provider::text AS provider,
            wallets.wallet_id::text AS provider_ref,
            wallets.metadata->>'address' AS address,
            wallets.asset_network_id::text AS asset_network_id,
            wallets.metadata AS wallet_metadata,
            wallets.status::text AS status,
            wallets.created_at,
            networks.id::text AS network_id,
            networks.code::text AS network_code,
            networks.name::text AS network_name,
            networks.type::text AS network_kind,
            networks.confirmations_required,
            networks.metadata AS network_metadata,
            networks.created_at AS network_created_at
          FROM wallets
          LEFT JOIN asset_networks ON asset_networks.id = wallets.asset_network_id
          LEFT JOIN networks ON networks.id = asset_networks.network_id
          WHERE wallets.id = $1::uuid
          LIMIT 1`,
          req.params.id
        );
        const wallet = walletRows[0] ? mapWallet(walletRows[0]) : existing;

        await req.auditLog?.({
          action: 'UPDATE_WALLET',
          entityType: 'WALLET',
          entityId: req.params.id,
          beforeSnapshot: existing,
          afterSnapshot: wallet,
        });

        res.json({ success: true, data: { wallet } });
      } catch (error) {
        next(error);
      }
    }
  );

  router.patch(
    '/asset-networks/:id',
    requirePermission(PERMISSIONS.WALLETS_WRITE),
    validateParams(z.object({ id: commonSchemas.uuid })),
    validateBody(assetNetworkPatchSchema),
    async (req, res, next) => {
      try {
        const existing = await getAssetNetworkRow(req.params.id);
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Asset network not found' });
          return;
        }

        const updates: string[] = [];
        const values: unknown[] = [];
        if (req.body.depositEnabled !== undefined) {
          values.push(req.body.depositEnabled);
          updates.push(`deposits_enabled = $${values.length}`);
        }
        if (req.body.withdrawalEnabled !== undefined) {
          values.push(req.body.withdrawalEnabled);
          updates.push(`withdrawals_enabled = $${values.length}`);
        }
        if (updates.length > 0) {
          values.push(req.params.id);
          await prisma.$executeRawUnsafe(
            `UPDATE asset_networks SET ${updates.join(', ')} WHERE id = $${values.length}::uuid`,
            ...values
          );
        }

        if (await tableExists(prisma, 'withdrawal_policies')) {
          const minWithdrawal = req.body.minWithdrawal ?? existing.min_withdrawal ?? '0';
          const withdrawalFee = req.body.withdrawalFee ?? existing.withdrawal_fee ?? '0';
          await prisma.$executeRawUnsafe(
            `INSERT INTO withdrawal_policies (
              asset_network_id,
              min_withdrawal_atoms,
              max_withdrawal_atoms,
              daily_limit_atoms,
              daily_limit_count,
              required_approvals,
              high_risk_approvals,
              enabled,
              metadata,
              updated_at
            )
            VALUES (
              $1::uuid,
              $2::numeric,
              NULL,
              NULL,
              NULL,
              1,
              2,
              TRUE,
              jsonb_build_object('withdrawalFeeAtoms', $3::text, 'flatFee', true),
              NOW()
            )
            ON CONFLICT (asset_network_id) DO UPDATE SET
              min_withdrawal_atoms = EXCLUDED.min_withdrawal_atoms,
              enabled = EXCLUDED.enabled,
              metadata = COALESCE(withdrawal_policies.metadata, '{}'::jsonb)
                || jsonb_build_object('withdrawalFeeAtoms', $3::text, 'flatFee', true),
              updated_at = NOW()`,
            req.params.id,
            minWithdrawal,
            withdrawalFee
          );
        }

        const assetNetworkRow = await getAssetNetworkRow(req.params.id);
        const assetNetwork = assetNetworkRow ? mapAssetNetwork(assetNetworkRow) : mapAssetNetwork(existing);

        await req.auditLog?.({
          action: 'UPDATE_ASSET_NETWORK',
          entityType: 'ASSET_NETWORK',
          entityId: req.params.id,
          beforeSnapshot: mapAssetNetwork(existing),
          afterSnapshot: assetNetwork,
        });

        res.json({ success: true, data: { assetNetwork } });
      } catch (error) {
        next(error);
      }
    }
  );

  return router;
}
