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

const walletQuerySchema = z.object({
  provider: z.enum(['BITGO', 'LOCAL_ANIMICA', 'OTHER']).optional(),
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

export function createWalletsRouter(
  prisma: PrismaClient,
  _config: Config,
  _logger: Logger
): Router {
  const router = Router();

  router.get(
    '/',
    requirePermission(PERMISSIONS.WALLETS_READ),
    validateQuery(walletQuerySchema),
    async (req, res, next) => {
      try {
        const { provider, purpose, active, page = 1, limit = 50 } = req.query as any;
        const walletWhere: any = {};
        if (provider) walletWhere.provider = provider;
        if (purpose) walletWhere.purpose = purpose;
        if (typeof active === 'boolean') walletWhere.isActive = active;

        const [wallets, total, assetNetworks, assets, networks] = await Promise.all([
          prisma.wallet.findMany({
            where: walletWhere,
            include: {
              network: true,
              _count: {
                select: {
                  assignedAddresses: true,
                },
              },
            },
            orderBy: [{ provider: 'asc' }, { purpose: 'asc' }, { createdAt: 'desc' }],
            skip: (page - 1) * limit,
            take: limit,
          }),
          prisma.wallet.count({ where: walletWhere }),
          prisma.assetNetwork.findMany({
            include: {
              asset: true,
              network: true,
              _count: {
                select: {
                  depositAddresses: true,
                  deposits: true,
                  withdrawals: true,
                },
              },
            },
            orderBy: [{ asset: { symbol: 'asc' } }, { network: { code: 'asc' } }],
          }),
          prisma.asset.findMany({ orderBy: { symbol: 'asc' } }),
          prisma.network.findMany({ orderBy: { code: 'asc' } }),
        ]);

        res.json({
          success: true,
          data: {
            wallets,
            assetNetworks,
            assets,
            networks,
            pagination: {
              page,
              limit,
              total,
              totalPages: Math.ceil(total / limit),
            },
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
        const existing = await prisma.wallet.findUnique({
          where: { id: req.params.id },
        });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Wallet not found' });
          return;
        }

        const wallet = await prisma.wallet.update({
          where: { id: req.params.id },
          data: {
            providerRef: req.body.providerRef,
            address: req.body.address,
            isActive: req.body.isActive,
          },
          include: { network: true },
        });

        await req.auditLog?.({
          action: 'UPDATE_WALLET',
          entityType: 'WALLET',
          entityId: wallet.id,
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
        const existing = await prisma.assetNetwork.findUnique({
          where: { id: req.params.id },
          include: { asset: true, network: true },
        });
        if (!existing) {
          res.status(404).json({ error: 'NotFound', message: 'Asset network not found' });
          return;
        }

        const assetNetwork = await prisma.assetNetwork.update({
          where: { id: req.params.id },
          data: {
            depositEnabled: req.body.depositEnabled,
            withdrawalEnabled: req.body.withdrawalEnabled,
            minWithdrawal: req.body.minWithdrawal,
            withdrawalFee: req.body.withdrawalFee,
          },
          include: { asset: true, network: true },
        });

        await req.auditLog?.({
          action: 'UPDATE_ASSET_NETWORK',
          entityType: 'ASSET_NETWORK',
          entityId: assetNetwork.id,
          beforeSnapshot: existing,
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
