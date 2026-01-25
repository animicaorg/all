/**
 * Rate Limiting Middleware
 * 
 * Prevents webhook flooding by limiting requests per IP per time window
 */

import type { Request, Response, NextFunction } from "express";
import type { Logger } from "pino";
import type { RedisClientType } from "redis";

export interface RateLimitConfig {
  windowMs: number; // Time window in milliseconds
  maxRequests: number; // Max requests per window
  keyPrefix: string; // Redis key prefix
}

/**
 * Create rate limiting middleware using Redis
 */
export function createRateLimiter(
  redis: RedisClientType,
  config: RateLimitConfig,
  logger: Logger
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    // Use IP address as key
    const ip = req.ip || req.socket.remoteAddress || "unknown";
    const key = `${config.keyPrefix}:${ip}`;

    try {
      // Get current count
      const current = await redis.get(key);
      const count = current ? parseInt(current, 10) : 0;

      if (count >= config.maxRequests) {
        logger.warn(
          { ip, count, limit: config.maxRequests },
          "Rate limit exceeded"
        );

        res.status(429).json({
          error: "Too Many Requests",
          message: "Rate limit exceeded. Please try again later.",
          retryAfter: Math.ceil(config.windowMs / 1000),
        });
        return;
      }

      // Increment counter
      const newCount = count + 1;
      await redis.set(key, newCount.toString(), {
        PX: config.windowMs, // Set expiry in milliseconds
        NX: count === 0, // Only set if doesn't exist for first request
      });

      // Add rate limit headers
      res.setHeader("X-RateLimit-Limit", config.maxRequests.toString());
      res.setHeader("X-RateLimit-Remaining", (config.maxRequests - newCount).toString());
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
