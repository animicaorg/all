/**
 * Private Account and Balance Endpoints
 * Requires API key authentication
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { LedgerClient } from '../../services/ledger_client.js';
import { UsersClient } from '../../services/users_client.js';
import { validate, type ValidatedRequest } from '../middleware/validation.js';
import type { ApiKeyAuthRequest } from '../middleware/api_key_auth.js';
import { requireScopes } from '../middleware/api_key_auth.js';
import { NotFoundError } from '../../utils/errors.js';

interface AuthenticatedRequest extends ApiKeyAuthRequest, ValidatedRequest {
  apiKey: {
    id: string;
    userId: string;
    scopes: string[];
  };
}

interface AccountResponse {
  user_id: string;
  email: string;
  status: string;
  kyc_status: string;
  kyc_tier?: string;
  created_at: string;
}

interface BalanceResponse {
  asset: string;
  available: string;
  locked: string;
  total: string;
}

/**
 * Create private accounts router
 */
export function createPrivateAccountsRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const ledgerClient = new LedgerClient(prisma, logger);
  const usersClient = new UsersClient(prisma, logger);

  /**
   * GET /api/v1/account
   * Returns authenticated user's account information
   */
  router.get(
    '/',
    requireScopes('account:read'),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;

        // Fetch user with profile
        const user = await usersClient.getUserProfile(userId);

        if (!user) {
          throw new NotFoundError('User account not found');
        }

        const response: AccountResponse = {
          user_id: user.id,
          email: user.email,
          status: user.status,
          kyc_status: user.kycStatus,
          kyc_tier: user.profile?.tier || undefined,
          created_at: user.createdAt.toISOString(),
        };

        logger.debug({ userId }, 'Account info retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to fetch account');
        next(error);
      }
    }
  );

  /**
   * GET /api/v1/balances
   * Returns all user balances across all assets
   */
  router.get(
    '/balances',
    requireScopes('balances:read'),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;

        // Fetch all user balances
        const balances = await ledgerClient.getUserBalances(userId);

        const response: BalanceResponse[] = balances.map((b) => ({
          asset: b.asset,
          available: b.available,
          locked: b.locked,
          total: b.total,
        }));

        logger.debug({ userId, count: response.length }, 'Balances retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to fetch balances');
        next(error);
      }
    }
  );

  return router;
}
