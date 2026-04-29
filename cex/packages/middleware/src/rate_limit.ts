/**
 * Enhanced Rate Limiting & Abuse Prevention
 * Provides per-route, per-IP, and per-user rate limiting with Redis backend
 */

import rateLimit from 'express-rate-limit';
import type { Options } from 'express-rate-limit';
import type { NextFunction, Request, Response } from 'express';
import type { Logger } from '@cex/observability';
import { RedisClientType } from 'redis';

/**
 * Rate limit configuration
 */
export interface RateLimitConfig {
  /**
   * Time window in milliseconds
   */
  windowMs: number;

  /**
   * Maximum requests per window
   */
  max: number;

  /**
   * Redis client (optional, falls back to memory store)
   */
  redis?: RedisClientType;

  /**
   * Key prefix for Redis
   */
  keyPrefix?: string;

  /**
   * Custom message for rate limit exceeded
   */
  message?: string;

  /**
   * Logger
   */
  logger?: Logger;

  /**
   * Skip function (e.g., skip for whitelisted IPs)
   */
  skip?: Options['skip'];
}

/**
 * Create a basic rate limiter
 */
export function createRateLimiter(config: RateLimitConfig) {
  const store = config.redis
    ? createRedisStore(config.redis, config.keyPrefix || 'rl')
    : undefined;

  return rateLimit({
    windowMs: config.windowMs,
    max: config.max,
    message: config.message || 'Too many requests, please try again later',
    standardHeaders: true,
    legacyHeaders: false,
    store,
    skip: config.skip,
    handler: (req, res) => {
      config.logger?.warn(
        {
          ip: req.ip,
          path: req.path,
          method: req.method,
        },
        'Rate limit exceeded'
      );

      res.status(429).json({
        error: 'RateLimitExceeded',
        message: config.message || 'Too many requests, please try again later',
        retryAfter: res.getHeader('Retry-After'),
      });
    },
  });
}

/**
 * Create a Redis-backed rate limit store
 */
function createRedisStore(redis: RedisClientType, prefix: string) {
  return {
    async increment(key: string): Promise<{ totalHits: number; resetTime: Date }> {
      const fullKey = `${prefix}:${key}`;
      const ttl = 60; // 1 minute default

      const multi = redis.multi();
      multi.incr(fullKey);
      multi.expire(fullKey, ttl);
      const results = await multi.exec();

      const totalHits = results?.[0] as number;
      const resetTime = new Date(Date.now() + ttl * 1000);

      return { totalHits, resetTime };
    },

    async decrement(key: string): Promise<void> {
      const fullKey = `${prefix}:${key}`;
      await redis.decr(fullKey);
    },

    async resetKey(key: string): Promise<void> {
      const fullKey = `${prefix}:${key}`;
      await redis.del(fullKey);
    },
  };
}

/**
 * Strict login rate limiter (5 attempts per 15 minutes)
 */
export function createLoginRateLimiter(config: { logger?: Logger; redis?: RedisClientType }) {
  return createRateLimiter({
    windowMs: 15 * 60 * 1000, // 15 minutes
    max: 5,
    message: 'Too many login attempts, please try again later',
    keyPrefix: 'login_rl',
    logger: config.logger,
    redis: config.redis,
  });
}

/**
 * API rate limiter (100 requests per minute)
 */
export function createApiRateLimiter(config: { logger?: Logger; redis?: RedisClientType }) {
  return createRateLimiter({
    windowMs: 60 * 1000, // 1 minute
    max: 100,
    keyPrefix: 'api_rl',
    logger: config.logger,
    redis: config.redis,
  });
}

/**
 * WebSocket connection rate limiter (10 connections per minute per IP)
 */
export function createWsConnectionLimiter(config: { logger?: Logger; redis?: RedisClientType }) {
  return createRateLimiter({
    windowMs: 60 * 1000, // 1 minute
    max: 10,
    message: 'Too many WebSocket connection attempts',
    keyPrefix: 'ws_conn_rl',
    logger: config.logger,
    redis: config.redis,
  });
}

/**
 * Order placement rate limiter (50 orders per minute)
 */
export function createOrderRateLimiter(config: { logger?: Logger; redis?: RedisClientType }) {
  return createRateLimiter({
    windowMs: 60 * 1000, // 1 minute
    max: 50,
    message: 'Too many order requests',
    keyPrefix: 'order_rl',
    logger: config.logger,
    redis: config.redis,
  });
}

/**
 * Ban list management
 */
export interface BanListConfig {
  redis: RedisClientType;
  logger?: Logger;
  defaultTtl?: number; // Default ban duration in seconds
}

export class BanList {
  private redis: RedisClientType;
  private logger?: Logger;
  private defaultTtl: number;
  private keyPrefix = 'ban';

  constructor(config: BanListConfig) {
    this.redis = config.redis;
    this.logger = config.logger;
    this.defaultTtl = config.defaultTtl || 3600; // 1 hour default
  }

  /**
   * Ban an IP address
   */
  async banIp(ip: string, reason: string, durationSec?: number): Promise<void> {
    const key = `${this.keyPrefix}:ip:${ip}`;
    const ttl = durationSec || this.defaultTtl;

    await this.redis.set(key, JSON.stringify({ reason, bannedAt: Date.now() }), {
      EX: ttl,
    });

    this.logger?.warn({ ip, reason, duration: ttl }, 'IP banned');
  }

  /**
   * Check if an IP is banned
   */
  async isBanned(ip: string): Promise<{ banned: boolean; reason?: string }> {
    const key = `${this.keyPrefix}:ip:${ip}`;
    const data = await this.redis.get(key);

    if (!data) {
      return { banned: false };
    }

    const parsed = JSON.parse(data);
    return { banned: true, reason: parsed.reason };
  }

  /**
   * Unban an IP address
   */
  async unbanIp(ip: string): Promise<void> {
    const key = `${this.keyPrefix}:ip:${ip}`;
    await this.redis.del(key);
    this.logger?.info({ ip }, 'IP unbanned');
  }

  /**
   * Ban a user by ID
   */
  async banUser(userId: string, reason: string, durationSec?: number): Promise<void> {
    const key = `${this.keyPrefix}:user:${userId}`;
    const ttl = durationSec || this.defaultTtl;

    await this.redis.set(key, JSON.stringify({ reason, bannedAt: Date.now() }), {
      EX: ttl,
    });

    this.logger?.warn({ userId, reason, duration: ttl }, 'User banned');
  }

  /**
   * Check if a user is banned
   */
  async isUserBanned(userId: string): Promise<{ banned: boolean; reason?: string }> {
    const key = `${this.keyPrefix}:user:${userId}`;
    const data = await this.redis.get(key);

    if (!data) {
      return { banned: false };
    }

    const parsed = JSON.parse(data);
    return { banned: true, reason: parsed.reason };
  }

  /**
   * List all banned IPs (for admin)
   */
  async listBannedIps(): Promise<Array<{ ip: string; reason: string; bannedAt: number }>> {
    const keys = await this.redis.keys(`${this.keyPrefix}:ip:*`);
    const result = [];

    for (const key of keys) {
      const data = await this.redis.get(key);
      if (data) {
        const parsed = JSON.parse(data);
        const ip = key.replace(`${this.keyPrefix}:ip:`, '');
        result.push({ ip, ...parsed });
      }
    }

    return result;
  }
}

/**
 * Middleware to check ban list
 */
export function createBanCheckMiddleware(banList: BanList) {
  return async (req: Request, res: Response, next: NextFunction) => {
    const ip = req.ip || req.socket.remoteAddress || 'unknown';

    const banStatus = await banList.isBanned(ip);
    if (banStatus.banned) {
      res.status(403).json({
        error: 'Banned',
        message: 'Your IP address has been banned',
        reason: banStatus.reason,
      });
      return;
    }

    next();
  };
}

/**
 * Track failed authentication attempts
 */
export interface AuthAttemptTracker {
  redis: RedisClientType;
  logger?: Logger;
  maxAttempts: number;
  windowMs: number;
  lockoutDuration: number;
}

export async function trackFailedAuth(
  config: AuthAttemptTracker,
  identifier: string // IP or user ID
): Promise<{ locked: boolean; attemptsRemaining: number }> {
  const key = `auth_attempts:${identifier}`;
  const lockKey = `auth_locked:${identifier}`;

  // Check if already locked
  const locked = await config.redis.exists(lockKey);
  if (locked) {
    return { locked: true, attemptsRemaining: 0 };
  }

  // Increment attempts
  const attempts = await config.redis.incr(key);

  if (attempts === 1) {
    // First attempt, set expiry
    await config.redis.expire(key, Math.floor(config.windowMs / 1000));
  }

  if (attempts >= config.maxAttempts) {
    // Lock account
    await config.redis.set(lockKey, '1', {
      EX: Math.floor(config.lockoutDuration / 1000),
    });

    config.logger?.warn(
      { identifier, attempts },
      'Account locked due to too many failed attempts'
    );

    return { locked: true, attemptsRemaining: 0 };
  }

  return { locked: false, attemptsRemaining: config.maxAttempts - attempts };
}

/**
 * Reset auth attempts on successful login
 */
export async function resetAuthAttempts(
  redis: RedisClientType,
  identifier: string
): Promise<void> {
  const key = `auth_attempts:${identifier}`;
  const lockKey = `auth_locked:${identifier}`;
  await redis.del(key);
  await redis.del(lockKey);
}
