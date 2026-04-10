/**
 * Rate Limiting Middleware
 * Supports IP-based, API key-based, and user-based rate limiting
 */

import type { Request, Response, NextFunction } from 'express';
import type { RedisClientType } from 'redis';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { RateLimitError } from '../../utils/errors.js';

export interface RateLimitConfig {
  windowMs: number;
  maxRequests: number;
  keyPrefix: string;
  burst?: number;
}

export interface AuthenticatedRequest extends Request {
  user?: {
    id: string;
    role: string;
  };
  apiKey?: {
    id: string;
    userId: string;
    scopes: string[];
  };
}

/**
 * Redis-based rate limiter using token bucket algorithm
 */
export function createRedisRateLimiter(
  redis: RedisClientType,
  config: RateLimitConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const authReq = req as AuthenticatedRequest;
      
      // Determine identifier
      const apiKeyId = authReq.apiKey?.id;
      const userId = authReq.user?.id;
      const ip = req.ip || req.socket.remoteAddress || 'unknown';
      
      const identifier = apiKeyId || userId || ip;
      const key = `${config.keyPrefix}:${identifier}`;

      // Use token bucket with burst support
      const burst = config.burst || config.maxRequests;
      const now = Date.now();
      const windowStart = now - config.windowMs;

      // Get current state
      const multi = redis.multi();
      multi.zRemRangeByScore(key, 0, windowStart); // Remove old entries
      multi.zCard(key); // Count current entries
      multi.expire(key, Math.ceil(config.windowMs / 1000));
      
      const results = await multi.exec();
      const current = (results?.[1] as number) || 0;

      // Check if over limit
      if (current >= config.maxRequests) {
        // Calculate reset time
        const oldestEntry = await redis.zRange(key, 0, 0, { REV: false });
        const resetAt = oldestEntry.length > 0 
          ? parseInt(oldestEntry[0]) + config.windowMs 
          : now + config.windowMs;
        const retryAfter = Math.max(0, resetAt - now);

        // Set headers
        res.setHeader('X-RateLimit-Limit', config.maxRequests.toString());
        res.setHeader('X-RateLimit-Remaining', '0');
        res.setHeader('X-RateLimit-Reset', new Date(resetAt).toISOString());
        res.setHeader('Retry-After', Math.ceil(retryAfter / 1000).toString());

        throw new RateLimitError(
          retryAfter,
          config.maxRequests,
          0,
          resetAt
        );
      }

      // Add new request
      await redis.zAdd(key, { score: now, value: `${now}-${Math.random()}` });

      // Set headers
      res.setHeader('X-RateLimit-Limit', config.maxRequests.toString());
      res.setHeader('X-RateLimit-Remaining', (config.maxRequests - current - 1).toString());

      next();
    } catch (error) {
      if (error instanceof RateLimitError) {
        throw error;
      }
      logger.error({ error, key: config.keyPrefix }, 'Rate limiter error');
      // Fail open on errors
      next();
    }
  };
}

/**
 * In-memory rate limiter fallback using sliding window
 */
export function createInMemoryRateLimiter(
  config: RateLimitConfig,
  logger: Logger
) {
  const store = new Map<string, number[]>();

  // Cleanup old entries periodically
  setInterval(() => {
    const now = Date.now();
    const windowStart = now - config.windowMs;
    
    for (const [key, timestamps] of store.entries()) {
      const filtered = timestamps.filter(ts => ts > windowStart);
      if (filtered.length === 0) {
        store.delete(key);
      } else {
        store.set(key, filtered);
      }
    }
  }, 60000); // Cleanup every minute

  return (req: Request, res: Response, next: NextFunction) => {
    try {
      const authReq = req as AuthenticatedRequest;
      
      const apiKeyId = authReq.apiKey?.id;
      const userId = authReq.user?.id;
      const ip = req.ip || req.socket.remoteAddress || 'unknown';
      
      const identifier = apiKeyId || userId || ip;
      const key = `${config.keyPrefix}:${identifier}`;
      const now = Date.now();
      const windowStart = now - config.windowMs;

      // Get current timestamps
      let timestamps = store.get(key) || [];
      
      // Remove old timestamps
      timestamps = timestamps.filter(ts => ts > windowStart);

      // Check if over limit
      if (timestamps.length >= config.maxRequests) {
        const oldestTs = timestamps[0];
        const resetAt = oldestTs + config.windowMs;
        const retryAfter = Math.max(0, resetAt - now);

        res.setHeader('X-RateLimit-Limit', config.maxRequests.toString());
        res.setHeader('X-RateLimit-Remaining', '0');
        res.setHeader('X-RateLimit-Reset', new Date(resetAt).toISOString());
        res.setHeader('Retry-After', Math.ceil(retryAfter / 1000).toString());

        throw new RateLimitError(
          retryAfter,
          config.maxRequests,
          0,
          resetAt
        );
      }

      // Add new timestamp
      timestamps.push(now);
      store.set(key, timestamps);

      // Set headers
      res.setHeader('X-RateLimit-Limit', config.maxRequests.toString());
      res.setHeader('X-RateLimit-Remaining', (config.maxRequests - timestamps.length).toString());

      next();
    } catch (error) {
      if (error instanceof RateLimitError) {
        throw error;
      }
      logger.error({ error }, 'In-memory rate limiter error');
      next();
    }
  };
}

/**
 * Factory function to create appropriate rate limiter
 */
export function createRateLimiter(
  redis: RedisClientType | null,
  config: RateLimitConfig,
  logger: Logger
) {
  if (redis) {
    return createRedisRateLimiter(redis, config, logger);
  } else {
    logger.warn('Using in-memory rate limiter - not suitable for production');
    return createInMemoryRateLimiter(config, logger);
  }
}

/**
 * Create rate limiters for different endpoints
 */
export function createRateLimiters(
  redis: RedisClientType | null,
  config: Config,
  logger: Logger
) {
  return {
    public: createRateLimiter(
      redis,
      {
        windowMs: config.RATE_LIMIT_PUBLIC_WINDOW_MS,
        maxRequests: config.RATE_LIMIT_PUBLIC_PER_IP,
        keyPrefix: 'ratelimit:public',
      },
      logger
    ),
    private: createRateLimiter(
      redis,
      {
        windowMs: config.RATE_LIMIT_PRIVATE_WINDOW_MS,
        maxRequests: config.RATE_LIMIT_PRIVATE_PER_KEY,
        keyPrefix: 'ratelimit:private',
        burst: config.RATE_LIMIT_PRIVATE_BURST,
      },
      logger
    ),
    user: createRateLimiter(
      redis,
      {
        windowMs: config.RATE_LIMIT_PRIVATE_WINDOW_MS,
        maxRequests: config.RATE_LIMIT_USER_AGGREGATE,
        keyPrefix: 'ratelimit:user',
      },
      logger
    ),
  };
}
