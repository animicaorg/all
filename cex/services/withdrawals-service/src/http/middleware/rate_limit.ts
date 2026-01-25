/**
 * Rate Limiting Middleware
 */

import type { Request, Response, NextFunction } from "express";
import type { RedisClientType } from "redis";
import type { Logger } from "pino";

interface RateLimitConfig {
  windowMs: number;
  maxRequests: number;
  keyPrefix: string;
}

/**
 * Redis-based rate limiting middleware
 */
export function createRateLimiter(
  redis: RedisClientType,
  config: RateLimitConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      // Use user ID or IP address as the key
      const userId = (req as any).user?.id;
      const identifier = userId || req.ip || "anonymous";
      const key = `${config.keyPrefix}:${identifier}`;

      // Get current count
      const current = await redis.incr(key);

      // Set expiry on first request
      if (current === 1) {
        await redis.pExpire(key, config.windowMs);
      }

      // Check if over limit
      if (current > config.maxRequests) {
        const ttl = await redis.pTTL(key);
        
        res.set("X-RateLimit-Limit", config.maxRequests.toString());
        res.set("X-RateLimit-Remaining", "0");
        res.set("X-RateLimit-Reset", new Date(Date.now() + ttl).toISOString());

        return res.status(429).json({
          error: "Too Many Requests",
          message: "Rate limit exceeded. Please try again later.",
        });
      }

      // Set rate limit headers
      res.set("X-RateLimit-Limit", config.maxRequests.toString());
      res.set("X-RateLimit-Remaining", (config.maxRequests - current).toString());

      next();
    } catch (error) {
      logger.error({ error }, "Rate limiter error");
      // On error, allow the request through (fail open)
      next();
    }
  };
}

/**
 * In-memory rate limiter fallback
 */
export function createInMemoryRateLimiter(
  config: RateLimitConfig,
  logger: Logger
) {
  const store = new Map<string, { count: number; resetAt: number }>();

  return (req: Request, res: Response, next: NextFunction) => {
    try {
      const userId = (req as any).user?.id;
      const identifier = userId || req.ip || "anonymous";
      const key = `${config.keyPrefix}:${identifier}`;
      const now = Date.now();

      let entry = store.get(key);

      // Clean up expired entry
      if (entry && entry.resetAt <= now) {
        store.delete(key);
        entry = undefined;
      }

      // Create new entry
      if (!entry) {
        entry = {
          count: 1,
          resetAt: now + config.windowMs,
        };
        store.set(key, entry);
      } else {
        entry.count++;
      }

      // Check if over limit
      if (entry.count > config.maxRequests) {
        res.set("X-RateLimit-Limit", config.maxRequests.toString());
        res.set("X-RateLimit-Remaining", "0");
        res.set("X-RateLimit-Reset", new Date(entry.resetAt).toISOString());

        return res.status(429).json({
          error: "Too Many Requests",
          message: "Rate limit exceeded. Please try again later.",
        });
      }

      // Set rate limit headers
      res.set("X-RateLimit-Limit", config.maxRequests.toString());
      res.set("X-RateLimit-Remaining", (config.maxRequests - entry.count).toString());

      next();
    } catch (error) {
      logger.error({ error }, "In-memory rate limiter error");
      next();
    }
  };
}
