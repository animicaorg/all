/**
 * CORS Middleware
 */

import cors from 'cors';
import type { Config } from '../../config.js';

export function createCorsMiddleware(config: Config) {
  const origins = config.CORS_ORIGIN.split(',').map((o) => o.trim());

  return cors({
    origin: origins.includes('*') ? '*' : origins,
    credentials: config.CORS_CREDENTIALS,
    methods: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH', 'OPTIONS'],
    allowedHeaders: [
      'Content-Type',
      'Authorization',
      'X-API-Key',
      'X-API-Timestamp',
      'X-API-Nonce',
      'X-API-Signature',
      'X-Request-ID',
      'Idempotency-Key',
    ],
    exposedHeaders: [
      'X-Request-ID',
      'X-RateLimit-Limit',
      'X-RateLimit-Remaining',
      'X-RateLimit-Reset',
    ],
  });
}
