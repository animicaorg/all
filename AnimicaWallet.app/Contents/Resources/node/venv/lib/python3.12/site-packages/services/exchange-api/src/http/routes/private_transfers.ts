/**
 * Private Transfer Endpoints
 * Deposits and withdrawals management
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { DepositsClient, type Deposit, DepositStatus } from '../../services/deposits_client.js';
import { WithdrawalsClient, type Withdrawal, WithdrawalStatus } from '../../services/withdrawals_client.js';
import { LedgerClient } from '../../services/ledger_client.js';
import { AuditRepository } from '../../db/repositories/audit_repo.js';
import { validate, type ValidatedRequest } from '../middleware/validation.js';
import type { ApiKeyAuthRequest } from '../middleware/api_key_auth.js';
import { requireScopes } from '../middleware/api_key_auth.js';
import {
  createPaginationSchema,
  createPaginationResponse,
  encodeCursor,
  decodeCursor,
} from '../middleware/pagination.js';
import { ValidationError, BadRequestError } from '../../utils/errors.js';
import { Decimal } from '@prisma/client/runtime/library';

interface AuthenticatedRequest extends ApiKeyAuthRequest, ValidatedRequest {
  apiKey: {
    id: string;
    userId: string;
    scopes: string[];
  };
}

/**
 * Validation schemas
 */
const listDepositsQuerySchema = (config: Config) =>
  z
    .object({
      asset: z.string().optional(),
      status: z.enum(['PENDING', 'CONFIRMING', 'COMPLETED', 'FAILED']).optional(),
    })
    .merge(createPaginationSchema(config));

const listWithdrawalsQuerySchema = (config: Config) =>
  z
    .object({
      asset: z.string().optional(),
      status: z.enum(['PENDING', 'APPROVED', 'PROCESSING', 'COMPLETED', 'FAILED', 'REJECTED', 'CANCELLED']).optional(),
    })
    .merge(createPaginationSchema(config));

const createWithdrawalSchema = z.object({
  asset: z.string().min(1),
  amount: z.string().min(1),
  address: z.string().min(1),
  memo: z.string().optional(),
  network: z.string().optional(),
  two_factor_code: z.string().optional(),
});

interface DepositResponse {
  deposit_id: string;
  asset: string;
  amount: string;
  status: string;
  tx_hash?: string;
  confirmations?: number;
  required_confirmations?: number;
  address: string;
  created_at: string;
  completed_at?: string;
}

interface WithdrawalResponse {
  withdrawal_id: string;
  asset: string;
  amount: string;
  fee: string;
  net_amount: string;
  status: string;
  address: string;
  tx_hash?: string;
  created_at: string;
  processed_at?: string;
  completed_at?: string;
  failure_reason?: string;
}

interface CreateWithdrawalResponse {
  withdrawal_id: string;
  status: string;
  message?: string;
}

/**
 * Transform deposit to response format
 */
function transformDeposit(deposit: Deposit): DepositResponse {
  return {
    deposit_id: deposit.id,
    asset: deposit.asset,
    amount: deposit.amount,
    status: deposit.status,
    tx_hash: deposit.txHash,
    confirmations: deposit.confirmations,
    required_confirmations: deposit.requiredConfirmations,
    address: deposit.address,
    created_at: deposit.createdAt.toISOString(),
    completed_at: deposit.completedAt?.toISOString(),
  };
}

/**
 * Transform withdrawal to response format
 */
function transformWithdrawal(withdrawal: Withdrawal): WithdrawalResponse {
  return {
    withdrawal_id: withdrawal.id,
    asset: withdrawal.asset,
    amount: withdrawal.amount,
    fee: withdrawal.fee,
    net_amount: withdrawal.netAmount,
    status: withdrawal.status,
    address: withdrawal.address,
    tx_hash: withdrawal.txHash,
    created_at: withdrawal.createdAt.toISOString(),
    processed_at: withdrawal.processedAt?.toISOString(),
    completed_at: withdrawal.completedAt?.toISOString(),
    failure_reason: withdrawal.failureReason,
  };
}

/**
 * Create private transfers router
 */
export function createPrivateTransfersRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const depositsClient = new DepositsClient(logger, config.DEPOSITS_SERVICE_URL);
  const withdrawalsClient = new WithdrawalsClient(logger, config.WITHDRAWALS_SERVICE_URL);
  const ledgerClient = new LedgerClient(prisma, logger);
  const auditRepo = new AuditRepository(prisma);

  /**
   * GET /api/v1/transfers/deposits
   * List user deposits with cursor pagination
   */
  router.get(
    '/deposits',
    requireScopes('transfers:read'),
    validate({ query: listDepositsQuerySchema(config) }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { asset, status, limit, cursor } = req.validated.query;

        // Build filters
        const filters: any = {};
        if (asset) {
          filters.asset = asset;
        }
        if (status) {
          filters.status = status as DepositStatus;
        }

        // Handle cursor for date filtering
        let startDate: Date | undefined;
        if (cursor) {
          try {
            const cursorData = decodeCursor(cursor);
            startDate = new Date(cursorData.created_at as string);
          } catch (error) {
            throw new ValidationError('Invalid cursor');
          }
        }

        // Fetch deposits from deposits service
        const depositsResponse = await depositsClient.getDeposits(
          userId,
          {
            ...filters,
            endDate: startDate,
          },
          {
            take: limit + 1,
            orderBy: 'createdAt',
            orderDirection: 'desc',
          }
        );

        // Transform to response format
        const deposits = depositsResponse.deposits.slice(0, limit).map(transformDeposit);

        // Create paginated response
        const response = createPaginationResponse(
          deposits,
          limit,
          (deposit) => encodeCursor({ created_at: deposit.created_at })
        );

        logger.debug({ userId, count: deposits.length }, 'Deposits retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to list deposits');
        next(error);
      }
    }
  );

  /**
   * GET /api/v1/transfers/withdrawals
   * List user withdrawals with cursor pagination
   */
  router.get(
    '/withdrawals',
    requireScopes('transfers:read'),
    validate({ query: listWithdrawalsQuerySchema(config) }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { asset, status, limit, cursor } = req.validated.query;

        // Build filters
        const filters: any = {};
        if (asset) {
          filters.asset = asset;
        }
        if (status) {
          filters.status = status as WithdrawalStatus;
        }

        // Handle cursor for date filtering
        let startDate: Date | undefined;
        if (cursor) {
          try {
            const cursorData = decodeCursor(cursor);
            startDate = new Date(cursorData.created_at as string);
          } catch (error) {
            throw new ValidationError('Invalid cursor');
          }
        }

        // Fetch withdrawals from withdrawals service
        const withdrawalsResponse = await withdrawalsClient.getWithdrawals(
          userId,
          {
            ...filters,
            endDate: startDate,
          },
          {
            take: limit + 1,
            orderBy: 'createdAt',
            orderDirection: 'desc',
          }
        );

        // Transform to response format
        const withdrawals = withdrawalsResponse.withdrawals.slice(0, limit).map(transformWithdrawal);

        // Create paginated response
        const response = createPaginationResponse(
          withdrawals,
          limit,
          (withdrawal) => encodeCursor({ created_at: withdrawal.created_at })
        );

        logger.debug({ userId, count: withdrawals.length }, 'Withdrawals retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to list withdrawals');
        next(error);
      }
    }
  );

  /**
   * POST /api/v1/withdrawals
   * Create a new withdrawal request
   */
  router.post(
    '/withdrawals',
    requireScopes('transfers:write'),
    validate({ body: createWithdrawalSchema }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { asset, amount, address, memo, network, two_factor_code } = req.validated.body;

        // Validate amount is positive
        const amountDecimal = new Decimal(amount);
        if (amountDecimal.lessThanOrEqualTo(0)) {
          throw new ValidationError('Amount must be positive');
        }

        // Check user has sufficient balance
        const availableBalance = await ledgerClient.getAvailableBalance(userId, asset);
        const availableDecimal = new Decimal(availableBalance);

        if (availableDecimal.lessThan(amountDecimal)) {
          throw new BadRequestError('Insufficient balance', {
            available: availableBalance,
            requested: amount,
          });
        }

        // Validate address format (basic check)
        if (address.length < 10) {
          throw new ValidationError('Invalid address format');
        }

        // Check idempotency
        const idempotencyKey = req.headers['idempotency-key'] as string | undefined;
        if (idempotencyKey) {
          // In production, check against a withdrawals_idempotency table or cache
          // For now, we'll check if a recent withdrawal exists with same params
          const recentWithdrawal = await prisma.$queryRaw<any[]>`
            SELECT id, status
            FROM withdrawals
            WHERE user_id = ${userId}::uuid
              AND asset = ${asset}
              AND amount = ${amount}::decimal
              AND address = ${address}
              AND created_at > NOW() - INTERVAL '1 hour'
            ORDER BY created_at DESC
            LIMIT 1
          `;

          if (recentWithdrawal && recentWithdrawal.length > 0) {
            logger.info({ userId, withdrawalId: recentWithdrawal[0].id }, 'Idempotent withdrawal request');
            return res.status(200).json({
              withdrawal_id: recentWithdrawal[0].id,
              status: recentWithdrawal[0].status,
              message: 'Withdrawal already exists',
            });
          }
        }

        // Submit withdrawal request to withdrawals service
        const withdrawalResponse = await withdrawalsClient.createWithdrawal({
          asset,
          amount,
          address,
          memo,
          twoFactorCode: two_factor_code,
        });

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'WITHDRAWAL_REQUESTED',
          entityType: 'WITHDRAWAL',
          entityId: withdrawalResponse.withdrawalId,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          after: {
            asset,
            amount,
            address: address.substring(0, 10) + '...', // Don't log full address
            network,
          },
        });

        const response: CreateWithdrawalResponse = {
          withdrawal_id: withdrawalResponse.withdrawalId,
          status: withdrawalResponse.status,
          message: withdrawalResponse.message,
        };

        logger.info({ userId, withdrawalId: withdrawalResponse.withdrawalId }, 'Withdrawal requested');
        res.status(201).json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to create withdrawal');
        next(error);
      }
    }
  );

  return router;
}
