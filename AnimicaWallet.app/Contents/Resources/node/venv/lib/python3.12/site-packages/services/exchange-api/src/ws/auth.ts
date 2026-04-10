/**
 * WebSocket Authentication
 * 
 * Handles API key authentication for WebSocket connections using the same
 * HMAC signature scheme as HTTP requests.
 */

import type { PrismaClient } from '@prisma/client';
import type { RedisClientType } from 'redis';
import type { Logger } from '../utils/logger.js';
import type { Config } from '../config.js';
import type { AuthMessage } from './protocol.js';
import {
  extractSignatureComponents,
  buildPrehashString,
  computeSignature,
  verifySignature,
} from '../http/middleware/signature_auth.js';

export interface AuthResult {
  success: boolean;
  userId?: string;
  apiKeyId?: string;
  scopes?: string[];
  error?: string;
}

interface ApiKeyData {
  id: string;
  userId: string;
  secretHash: string;
  scopes: unknown;
  revokedAt: Date | null;
}

/**
 * Validates request timestamp is within acceptable window
 */
function validateTimestamp(timestamp: number, windowMs: number): boolean {
  if (!timestamp || timestamp <= 0) {
    return false;
  }

  const now = Date.now();
  const diff = Math.abs(now - timestamp);
  return diff <= windowMs;
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
 * Checks if nonce has been used (Redis)
 */
async function checkNonceInRedis(
  redis: RedisClientType,
  apiKeyId: string,
  nonce: string,
  timestamp: number,
  ttlMs: number
): Promise<boolean> {
  const key = `nonce:${apiKeyId}:${nonce}`;
  const ttlSeconds = Math.ceil(ttlMs / 1000);

  // Use SET with NX (only if not exists) and EX (expiry in seconds)
  const result = await redis.set(key, timestamp.toString(), {
    NX: true,
    EX: ttlSeconds,
  });

  // Returns null if key already exists (nonce already used)
  return result !== null;
}

/**
 * Checks if nonce has been used (Database)
 */
async function checkNonceInDb(
  prisma: PrismaClient,
  apiKeyId: string,
  nonce: string,
  timestamp: number,
  ttlMs: number
): Promise<boolean> {
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
    return true;
  } catch (error: any) {
    // Check for unique constraint violation (nonce already used)
    if (error.code === 'P2002') {
      return false;
    }
    throw error;
  }
}

/**
 * Updates API key last used timestamp (fire and forget)
 */
function updateLastUsed(
  prisma: PrismaClient,
  apiKeyId: string,
  logger: Logger
): void {
  prisma.apiKey
    .update({
      where: { id: apiKeyId },
      data: { lastUsedAt: new Date() },
    })
    .catch((error) => {
      logger.warn({ error, apiKeyId }, 'Failed to update lastUsedAt');
    });
}

/**
 * Authenticates a WebSocket connection using API key and HMAC signature
 * 
 * @param msg - Auth message from client
 * @param prisma - Prisma client
 * @param redis - Redis client (optional, falls back to DB)
 * @param config - Service configuration
 * @param logger - Logger instance
 * @returns Authentication result with user/key info or error
 */
export async function authenticateWebSocket(
  msg: AuthMessage,
  prisma: PrismaClient,
  redis: RedisClientType | null,
  config: Config,
  logger: Logger
): Promise<AuthResult> {
  try {
    // Validate required fields
    if (!msg.apiKey || !msg.timestamp || !msg.nonce || !msg.signature) {
      return {
        success: false,
        error: 'Missing required authentication fields',
      };
    }

    // Validate timestamp
    if (!validateTimestamp(msg.timestamp, config.API_KEY_TIMESTAMP_WINDOW_MS)) {
      return {
        success: false,
        error: 'Request timestamp outside acceptable window',
      };
    }

    // Look up API key
    const apiKey = await prisma.apiKey.findUnique({
      where: { keyId: msg.apiKey },
      select: {
        id: true,
        userId: true,
        secretHash: true,
        scopes: true,
        revokedAt: true,
      },
    });

    if (!apiKey) {
      logger.warn({ keyId: msg.apiKey }, 'Invalid API key for WebSocket auth');
      return {
        success: false,
        error: 'Invalid API key',
      };
    }

    // Check if key is revoked
    if (apiKey.revokedAt) {
      logger.warn({ keyId: msg.apiKey }, 'Revoked API key used for WebSocket auth');
      return {
        success: false,
        error: 'API key has been revoked',
      };
    }

    // Build signature payload for WebSocket auth
    // Format: <timestamp>\n<nonce>\nWS\n/\n\n
    const components = extractSignatureComponents(
      msg.timestamp.toString(),
      msg.nonce,
      'WS',
      '/',
      '',
      ''
    );

    const prehash = buildPrehashString(components);
    const secret = apiKey.secretHash;
    const expectedSignature = computeSignature(secret, prehash);

    // Verify signature using timing-safe comparison
    if (!verifySignature(expectedSignature, msg.signature)) {
      logger.warn({ keyId: msg.apiKey }, 'Invalid signature for WebSocket auth');
      return {
        success: false,
        error: 'Invalid signature',
      };
    }

    // Check and store nonce
    let nonceValid: boolean;
    try {
      if (redis) {
        nonceValid = await checkNonceInRedis(
          redis,
          apiKey.id,
          msg.nonce,
          msg.timestamp,
          config.API_KEY_NONCE_TTL_MS
        );
      } else {
        nonceValid = await checkNonceInDb(
          prisma,
          apiKey.id,
          msg.nonce,
          msg.timestamp,
          config.API_KEY_NONCE_TTL_MS
        );
      }
    } catch (error) {
      logger.error({ error, keyId: msg.apiKey }, 'Nonce check failed');
      return {
        success: false,
        error: 'Authentication failed',
      };
    }

    if (!nonceValid) {
      return {
        success: false,
        error: 'Nonce already used',
      };
    }

    // Update last used timestamp (async, don't wait)
    updateLastUsed(prisma, apiKey.id, logger);

    logger.info(
      { keyId: msg.apiKey, userId: apiKey.userId },
      'WebSocket authenticated successfully'
    );

    return {
      success: true,
      userId: apiKey.userId,
      apiKeyId: apiKey.id,
      scopes: parseScopes(apiKey.scopes),
    };
  } catch (error) {
    logger.error({ error }, 'WebSocket authentication error');
    return {
      success: false,
      error: 'Internal authentication error',
    };
  }
}

/**
 * Checks if authenticated connection has required scope
 */
export function hasScope(scopes: string[] | undefined, required: string): boolean {
  if (!scopes) {
    return false;
  }
  return scopes.includes(required);
}
