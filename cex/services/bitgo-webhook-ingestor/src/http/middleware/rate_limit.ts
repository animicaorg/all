/**
 * Rate Limiting Middleware
 * 
 * Prevents webhook flooding by limiting requests per IP per time window
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "pino";

interface RedisRateLimitClient {
  ping(): Promise<string>;
  incr(key: string): Promise<number>;
  pexpire(key: string, milliseconds: number): Promise<number>;
  pttl(key: string): Promise<number>;
}

export interface RateLimitConfig {
  windowMs: number; // Time window in milliseconds
  maxRequests: number; // Max requests per window
  keyPrefix: string; // Redis key prefix
}

/**
 * Create rate limiting middleware using Redis
 */
export function createRateLimiter(
  redis: RedisRateLimitClient,
  config: RateLimitConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    const key = `${config.keyPrefix}:${ip}`;

    try {
      const count = await redis.incr(key);

      if (count >= config.maxRequests) {
        logger.warn(
          { ip, count, limit: config.maxRequests },
          "Rate limit exceeded"
        );

        const ttl = await redis.pttl(key);
        res.status(429).json({
          error: "Too Many Requests",
          message: "Rate limit exceeded. Please try again later.",
          retryAfter: Math.ceil(Math.max(ttl, 0) / 1000),
        });
        return;
      }

      if (count === 1) {
        await redis.pexpire(key, config.windowMs);
      }

      // Add rate limit headers
      res.setHeader("X-RateLimit-Limit", config.maxRequests.toString());
      res.setHeader("X-RateLimit-Remaining", (config.maxRequests - count).toString());
      res.setHeader(
        "X-RateLimit-Reset",
        new Date(Date.now() + config.windowMs).toISOString()
      );

      next();
    } catch (error) {
      logger.error({ error, ip }, "Rate limiter error");
      // Fail open - allow request if rate limiter fails
      next();
    }
  };
}

/**
 * Simple in-memory rate limiter (for testing/dev)
 */
export function createInMemoryRateLimiter(
  config: RateLimitConfig,
  logger: Logger
) {
  const store = new Map<string, { count: number; resetAt: number }>();

  // Cleanup expired entries periodically
  setInterval(() => {
    const now = Date.now();
    for (const [key, value] of store.entries()) {
      if (value.resetAt < now) {
        store.delete(key);
      }
    }
  }, config.windowMs);

  return (req: Request, res: Response, next: NextFunction) => {
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    const key = `${config.keyPrefix}:${ip}`;
    const now = Date.now();

    let entry = store.get(key);

    // Reset if window expired
    if (entry && entry.resetAt < now) {
      entry = undefined;
      store.delete(key);
    }

    if (!entry) {
      entry = { count: 0, resetAt: now + config.windowMs };
      store.set(key, entry);
    }

    if (entry.count >= config.maxRequests) {
      logger.warn(
        { ip, count: entry.count, limit: config.maxRequests },
        "Rate limit exceeded"
      );

      res.status(429).json({
        error: "Too Many Requests",
        message: "Rate limit exceeded. Please try again later.",
        retryAfter: Math.ceil((entry.resetAt - now) / 1000),
      });
      return;
    }

    entry.count++;

    // Add rate limit headers
    res.setHeader("X-RateLimit-Limit", config.maxRequests.toString());
    res.setHeader("X-RateLimit-Remaining", (config.maxRequests - entry.count).toString());
    res.setHeader("X-RateLimit-Reset", new Date(entry.resetAt).toISOString());

    next();
  };
}
