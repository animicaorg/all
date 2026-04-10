/**
 * API Key Authentication Middleware
 * 
 * Provides secure API key authentication with HMAC signature verification,
 * replay protection via nonces, and timestamp validation.
 * 
 * Security features:
 * - HMAC-SHA256 signature verification with timing-safe comparison
 * - Nonce-based replay attack prevention
 * - Timestamp window validation (±30s default)
 * - IP allowlist enforcement
 * - Automatic key revocation checking
 * - Redis-backed nonce storage with DB fallback
 */

import type { Request, Response, NextFunction } from 'express';
import type { PrismaClient } from '@prisma/client';
import type { RedisClientType } from 'redis';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { UnauthorizedError } from '../../utils/errors.js';
import {
  extractSignatureComponents,
  buildPrehashString,
  computeSignature,
  verifySignature,
} from './signature_auth.js';

export interface ApiKeyAuthRequest extends Request {
  apiKey?: {
    id: string;
    userId: string;
    scopes: string[];
  };
  rawBody?: string | Buffer;
}

interface ApiKeyData {
  id: string;
  userId: string;
  secretHash: string;
  scopes: unknown;
  ipAllowlist: unknown;
  revokedAt: Date | null;
}

/**
 * Validates request timestamp is within acceptable window
 */
function validateTimestamp(
  timestampStr: string,
  windowMs: number
): { valid: boolean; timestamp: number } {
  const timestamp = parseInt(timestampStr, 10);
  
  if (isNaN(timestamp) || timestamp <= 0) {
    return { valid: false, timestamp: 0 };
  }

  const now = Date.now();
  const diff = Math.abs(now - timestamp);

  return {
    valid: diff <= windowMs,
    timestamp,
  };
}

/**
 * Checks if IP address is in allowlist (if configured)
 */
function checkIpAllowlist(
  clientIp: string | undefined,
  allowlist: unknown
): boolean {
  // If no allowlist configured, allow all
  if (!allowlist || !Array.isArray(allowlist) || allowlist.length === 0) {
    return true;
  }

  // If no client IP detected, reject
  if (!clientIp) {
    return false;
  }

  // Check if IP is in allowlist
  return allowlist.includes(clientIp);
}

/**
 * Stores nonce in Redis with TTL
 */
async function storeNonceInRedis(
  redis: RedisClientType,
  apiKeyId: string,
  nonce: string,
  timestamp: number,
  ttlMs: number
): Promise<void> {
  const key = `nonce:${apiKeyId}:${nonce}`;
  const ttlSeconds = Math.ceil(ttlMs / 1000);
  
  // Use SET with NX (only if not exists) and EX (expiry in seconds)
  const result = await redis.set(key, timestamp.toString(), {
    NX: true,
    EX: ttlSeconds,
  });

  if (!result) {
    throw new UnauthorizedError('Nonce already used');
  }
}

/**
 * Stores nonce in database with expiry
 */
async function storeNonceInDb(
  prisma: PrismaClient,
  apiKeyId: string,
  nonce: string,
  timestamp: number,
  ttlMs: number
): Promise<void> {
  const expiresAt = new Date(Date.now() + ttlMs);

  try {
    await prisma.apiNonce.create({
      data: {
        apiKeyId,
        nonce,
        timestamp: BigInt(timestamp),
        expiresAt,
      },
    });
  } catch (error: any) {
    // Check for unique constraint violation (nonce already used)
    if (error.code === 'P2002') {
      throw new UnauthorizedError('Nonce already used');
    }
    throw error;
  }
}

/**
 * Cleans up expired nonces from database (should run periodically)
 */
async function cleanupExpiredNonces(
  prisma: PrismaClient,
  logger: Logger
): Promise<void> {
  try {
    const result = await prisma.apiNonce.deleteMany({
      where: {
        expiresAt: {
          lt: new Date(),
        },
      },
    });
    
    if (result.count > 0) {
      logger.debug({ count: result.count }, 'Cleaned up expired nonces');
    }
  } catch (error) {
    logger.error({ error }, 'Failed to cleanup expired nonces');
  }
}

/**
 * Updates API key last used timestamp
 */
async function updateLastUsed(
  prisma: PrismaClient,
  apiKeyId: string,
  logger: Logger
): Promise<void> {
  try {
    await prisma.apiKey.update({
      where: { id: apiKeyId },
      data: { lastUsedAt: new Date() },
    });
  } catch (error) {
    // Log but don't fail the request if this fails
    logger.warn({ error, apiKeyId }, 'Failed to update lastUsedAt');
  }
}

/**
 * Parses scopes from JSON field
 */
function parseScopes(scopes: unknown): string[] {
  if (Array.isArray(scopes)) {
    return scopes.filter((s): s is string => typeof s === 'string');
  }
  return [];
}

/**
 * Extracts request body for signature verification
 * Handles raw body (if available) or serializes parsed body
 */
function extractRequestBody(req: ApiKeyAuthRequest): string {
  // Check for raw body (set by raw body middleware)
  if (req.rawBody) {
    if (typeof req.rawBody === 'string') {
      return req.rawBody;
    }
    if (Buffer.isBuffer(req.rawBody)) {
      return req.rawBody.toString('utf8');
    }
  }
  
  // Serialize parsed body if it's an object
  if (req.body && typeof req.body === 'object') {
    return JSON.stringify(req.body);
  }
  
  // Return body as-is if it's a string, or empty string
  return req.body || '';
}

/**
 * Creates API key authentication middleware
 * 
 * Required headers:
 * - X-API-KEY: Key identifier (first 8-16 chars)
 * - X-API-TIMESTAMP: Unix timestamp in milliseconds
 * - X-API-NONCE: Unique nonce (UUID or increment)
 * - X-API-SIGNATURE: Base64-encoded HMAC-SHA256 signature
 * 
 * Signature is computed over:
 * <timestamp>\n<nonce>\n<method>\n<path>\n<query>\n<body_sha256_hex>
 */
export function createApiKeyAuthMiddleware(
  prisma: PrismaClient,
  redis: RedisClientType | null,
  config: Config,
  logger: Logger
) {
  // Start periodic cleanup if using database (every 5 minutes)
  // Note: In production, consider using a separate cleanup job or shutdown hook
  let cleanupInterval: NodeJS.Timeout | undefined;
  
  if (!redis) {
    cleanupInterval = setInterval(() => {
      cleanupExpiredNonces(prisma, logger).catch((error) => {
        logger.error({ error }, 'Nonce cleanup error');
      });
    }, 300000);
    
    // Allow cleanup to be stopped (for testing or graceful shutdown)
    if (typeof cleanupInterval === 'object' && 'unref' in cleanupInterval) {
      cleanupInterval.unref();
    }
  }

  return async (
    req: ApiKeyAuthRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      // Extract headers
      const keyId = req.headers['x-api-key'] as string | undefined;
      const timestampStr = req.headers['x-api-timestamp'] as string | undefined;
      const nonce = req.headers['x-api-nonce'] as string | undefined;
      const providedSignature = req.headers['x-api-signature'] as string | undefined;

      // Validate required headers
      if (!keyId || !timestampStr || !nonce || !providedSignature) {
        throw new UnauthorizedError('Missing required authentication headers');
      }

      // Validate timestamp
      const { valid: timestampValid, timestamp } = validateTimestamp(
        timestampStr,
        config.API_KEY_TIMESTAMP_WINDOW_MS
      );

      if (!timestampValid) {
        throw new UnauthorizedError('Request timestamp outside acceptable window');
      }

      // Look up API key
      const apiKey = await prisma.apiKey.findUnique({
        where: { keyId },
        select: {
          id: true,
          userId: true,
          secretHash: true,
          scopes: true,
          ipAllowlist: true,
          revokedAt: true,
        },
      });

      if (!apiKey) {
        throw new UnauthorizedError('Invalid API key');
      }

      // Check if key is revoked
      if (apiKey.revokedAt) {
        logger.warn({ keyId }, 'Attempted use of revoked API key');
        throw new UnauthorizedError('API key has been revoked');
      }

      // Check IP allowlist
      const clientIp = req.ip || req.socket.remoteAddress;
      if (!checkIpAllowlist(clientIp, apiKey.ipAllowlist)) {
        logger.warn({ keyId, clientIp }, 'IP not in allowlist');
        throw new UnauthorizedError('IP address not authorized');
      }

      // Extract request body for hashing
      const body = extractRequestBody(req);
      
      // Extract query string from URL
      const url = req.originalUrl || req.url || '';
      const queryString = url.includes('?') ? url.split('?')[1] : '';

      // Extract signature components
      const components = extractSignatureComponents(
        timestampStr,
        nonce,
        req.method,
        req.path,
        queryString,
        body
      );

      // Build prehash string
      const prehash = buildPrehashString(components);

      // IMPORTANT: Secret Storage Design
      // The secretHash field stores the HMAC secret needed for signature verification.
      // Despite the "Hash" name, HMAC signatures require the actual secret, not a hash.
      // 
      // Production recommendation: Encrypt secrets at rest using KMS/envelope encryption.
      // The field name is kept as "secretHash" for schema compatibility, but contains
      // the plaintext secret (or encrypted secret that we decrypt here in production).
      const secret = apiKey.secretHash;
      
      // Compute expected signature
      const expectedSignature = computeSignature(secret, prehash);

      // Verify signature using timing-safe comparison
      if (!verifySignature(expectedSignature, providedSignature)) {
        logger.warn({ keyId }, 'Invalid signature');
        throw new UnauthorizedError('Invalid signature');
      }

      // Check and store nonce
      try {
        if (redis) {
          await storeNonceInRedis(
            redis,
            apiKey.id,
            nonce,
            timestamp,
            config.API_KEY_NONCE_TTL_MS
          );
        } else {
          await storeNonceInDb(
            prisma,
            apiKey.id,
            nonce,
            timestamp,
            config.API_KEY_NONCE_TTL_MS
          );
        }
      } catch (error) {
        if (error instanceof UnauthorizedError) {
          throw error;
        }
        logger.error({ error, keyId }, 'Nonce storage failed');
        throw new UnauthorizedError('Authentication failed');
      }

      // Update last used timestamp (async, don't wait)
      updateLastUsed(prisma, apiKey.id, logger).catch(() => {
        // Errors already logged in updateLastUsed
      });

      // Attach API key info to request
      req.apiKey = {
        id: apiKey.id,
        userId: apiKey.userId,
        scopes: parseScopes(apiKey.scopes),
      };

      logger.debug({ keyId, userId: apiKey.userId }, 'API key authenticated');

      next();
    } catch (error) {
      if (error instanceof UnauthorizedError) {
        // Already properly formatted
        next(error);
      } else {
        // Log unexpected errors but don't leak details
        logger.error({ error }, 'API key authentication error');
        next(new UnauthorizedError('Authentication failed'));
      }
    }
  };
}

/**
 * Optional middleware to check specific scope requirements
 */
export function requireScopes(...requiredScopes: string[]) {
  return (req: ApiKeyAuthRequest, res: Response, next: NextFunction) => {
    if (!req.apiKey) {
      throw new UnauthorizedError('API key authentication required');
    }

    const hasAllScopes = requiredScopes.every((scope) =>
      req.apiKey!.scopes.includes(scope)
    );

    if (!hasAllScopes) {
      throw new UnauthorizedError(
        'Insufficient permissions',
        { required_scopes: requiredScopes }
      );
    }

    next();
  };
}
