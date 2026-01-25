/**
 * Rate Limiting Middleware
 * Protects endpoints from abuse with Redis-backed rate limiting
 */

import rateLimit from 'express-rate-limit';
import type { Config } from '../../config.js';

/**
 * Create rate limiter for login attempts
 */
export function createLoginRateLimiter(config: Config) {
  return rateLimit({
    windowMs: config.RATE_LIMIT_LOGIN_WINDOW_MS,
    max: config.RATE_LIMIT_LOGIN_MAX,
    message: {
      error: 'TooManyRequests',
      message: 'Too many login attempts. Please try again later.',
    },
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: (req) => {
      // Rate limit by IP and email
      const email = req.body?.email || 'unknown';
      return `login:${req.ip}:${email}`;
    },
  });
}

/**
 * Create rate limiter for admin endpoints
 */
export function createAdminRateLimiter(config: Config) {
  return rateLimit({
    windowMs: config.RATE_LIMIT_ADMIN_WINDOW_MS,
    max: config.RATE_LIMIT_ADMIN_PER_SESSION,
    message: {
      error: 'TooManyRequests',
      message: 'Too many requests. Please slow down.',
    },
    standardHeaders: true,
    legacyHeaders: false,
    keyGenerator: (req) => {
      // Rate limit by session ID if authenticated, otherwise by IP
      return req.session?.id || req.ip || 'unknown';
    },
    skip: (req) => {
      // Skip rate limiting for health checks
      return req.path === '/health' || req.path === '/admin/v1/health';
    },
  });
}
