/**
 * API Key Management Endpoints
 * Admin and internal use for managing API keys
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { ApiKeysRepository } from '../../db/repositories/api_keys_repo.js';
import { AuditRepository } from '../../db/repositories/audit_repo.js';
import { validate, type ValidatedRequest } from '../middleware/validation.js';
import type { ApiKeyAuthRequest } from '../middleware/api_key_auth.js';
import { requireScopes } from '../middleware/api_key_auth.js';
import { NotFoundError, ForbiddenError } from '../../utils/errors.js';

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
const createApiKeySchema = z.object({
  name: z.string().min(1).max(100),
  scopes: z.array(z.string()).min(1),
  ip_allowlist: z.array(z.string().ip()).optional(),
});

const deleteApiKeyParamsSchema = z.object({
  id: z.string().uuid(),
});

interface ApiKeyResponse {
  id: string;
  name: string;
  key_id: string;
  scopes: string[];
  ip_allowlist: string[] | null;
  created_at: string;
  last_used_at?: string;
  revoked_at?: string;
}

interface CreateApiKeyResponse extends ApiKeyResponse {
  secret: string;
  warning: string;
}

/**
 * Valid scopes for API keys
 */
const VALID_SCOPES = [
  'account:read',
  'balances:read',
  'orders:read',
  'orders:write',
  'transfers:read',
  'transfers:write',
  'admin',
];

/**
 * Validate scopes
 */
function validateScopes(scopes: string[]): void {
  const invalidScopes = scopes.filter((scope) => !VALID_SCOPES.includes(scope));
  if (invalidScopes.length > 0) {
    throw new Error(`Invalid scopes: ${invalidScopes.join(', ')}`);
  }
}

/**
 * Transform API key to response format
 */
function transformApiKey(
  apiKey: any,
  includeSecret?: { keyId: string; secret: string }
): ApiKeyResponse | CreateApiKeyResponse {
  const base: ApiKeyResponse = {
    id: apiKey.id,
    name: apiKey.name,
    key_id: includeSecret?.keyId || apiKey.keyId,
    scopes: Array.isArray(apiKey.scopes) ? apiKey.scopes : [],
    ip_allowlist: Array.isArray(apiKey.ipAllowlist) ? apiKey.ipAllowlist : null,
    created_at: apiKey.createdAt.toISOString(),
    last_used_at: apiKey.lastUsedAt?.toISOString(),
    revoked_at: apiKey.revokedAt?.toISOString(),
  };

  if (includeSecret) {
    return {
      ...base,
      secret: includeSecret.secret,
      warning: 'Save this secret securely. It will not be shown again.',
    } as CreateApiKeyResponse;
  }

  return base;
}

/**
 * Create API key management router
 */
export function createAuthRouter(
  prisma: PrismaClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const apiKeysRepo = new ApiKeysRepository(prisma);
  const auditRepo = new AuditRepository(prisma);

  /**
   * POST /api/v1/auth/api-keys
   * Create a new API key for the authenticated user
   * 
   * Requires 'admin' scope or internal authentication.
   * In production, this should be restricted to admin users or
   * available through a separate authenticated web UI.
   */
  router.post(
    '/api-keys',
    requireScopes('admin'),
    validate({ body: createApiKeySchema }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { name, scopes, ip_allowlist } = req.validated.body;

        // Validate scopes
        try {
          validateScopes(scopes);
        } catch (error: any) {
          throw new Error(error.message);
        }

        // Prevent non-admin users from creating admin keys
        if (scopes.includes('admin')) {
          const hasAdminScope = req.apiKey.scopes.includes('admin');
          if (!hasAdminScope) {
            throw new ForbiddenError('Cannot create API keys with admin scope');
          }
        }

        // Check if user has too many API keys
        const existingKeys = await apiKeysRepo.getByUserId(userId);
        const activeKeys = existingKeys.filter((k) => !k.revokedAt);

        const MAX_KEYS_PER_USER = 10;
        if (activeKeys.length >= MAX_KEYS_PER_USER) {
          throw new Error(`Maximum of ${MAX_KEYS_PER_USER} active API keys allowed`);
        }

        // Create the API key
        const result = await apiKeysRepo.createApiKey({
          userId,
          name,
          scopes,
          ipAllowlist: ip_allowlist,
        });

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'API_KEY_CREATED',
          entityType: 'API_KEY',
          entityId: result.apiKey.id,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          after: {
            name,
            scopes,
            ip_allowlist,
          },
        });

        const response = transformApiKey(result.apiKey, {
          keyId: result.keyId,
          secret: result.secret,
        }) as CreateApiKeyResponse;

        logger.info({ userId, apiKeyId: result.apiKey.id }, 'API key created');
        res.status(201).json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to create API key');
        next(error);
      }
    }
  );

  /**
   * GET /api/v1/auth/api-keys
   * List all API keys for the authenticated user
   * 
   * Requires 'admin' scope to manage API keys.
   */
  router.get(
    '/api-keys',
    requireScopes('admin'),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;

        // Fetch all API keys for user
        const apiKeys = await apiKeysRepo.getByUserId(userId);

        // Transform to response format (without secrets)
        const response = apiKeys.map((key) => transformApiKey(key));

        logger.debug({ userId, count: response.length }, 'API keys retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to list API keys');
        next(error);
      }
    }
  );

  /**
   * DELETE /api/v1/auth/api-keys/:id
   * Revoke an API key
   * 
   * Requires 'admin' scope.
   */
  router.delete(
    '/api-keys/:id',
    requireScopes('admin'),
    validate({ params: deleteApiKeyParamsSchema }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const apiKeyId = req.validated.params.id;

        // Verify API key belongs to user
        const apiKey = await prisma.apiKey.findUnique({
          where: { id: apiKeyId },
        });

        if (!apiKey) {
          throw new NotFoundError('API key not found');
        }

        if (apiKey.userId !== userId) {
          throw new NotFoundError('API key not found');
        }

        if (apiKey.revokedAt) {
          throw new Error('API key is already revoked');
        }

        // Prevent revoking the current API key being used
        if (apiKey.id === req.apiKey.id) {
          throw new Error('Cannot revoke the API key currently in use');
        }

        // Revoke the API key
        const revokedKey = await apiKeysRepo.revokeApiKey(apiKeyId);

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'API_KEY_REVOKED',
          entityType: 'API_KEY',
          entityId: apiKeyId,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          before: { status: 'active' },
          after: { status: 'revoked' },
        });

        const response = transformApiKey(revokedKey);

        logger.info({ userId, apiKeyId }, 'API key revoked');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId, apiKeyId: req.params.id }, 'Failed to revoke API key');
        next(error);
      }
    }
  );

  return router;
}
