import rateLimit from 'express-rate-limit';
import type { Config } from '../config.js';

export function createRateLimiter(config: Config) {
  return rateLimit({
    windowMs: config.USDAN_API_RATE_LIMIT_WINDOW_MS,
    max: config.USDAN_API_RATE_LIMIT_MAX,
    standardHeaders: true,
    legacyHeaders: false
  });
}
