/**
 * Example: API Key Authentication Integration
 * 
 * This example demonstrates how to integrate the API key authentication
 * middleware into your Express application.
 */

import express from 'express';
import { createApiKeyAuthMiddleware, requireScopes } from '../middleware/index.js';
import { prisma } from '../../db/client.js';
import { loadConfig } from '../../config.js';
import { createLogger } from '../../utils/logger.js';
import type { RedisClientType } from 'redis';

// Mock Redis client (replace with actual Redis connection)
const redis: RedisClientType | null = null;

// Load configuration
const config = loadConfig();
const logger = createLogger(config);

// Create middleware
const apiKeyAuth = createApiKeyAuthMiddleware(prisma, redis, config, logger);

// Create Express app
const app = express();

// Parse JSON bodies (required for signature verification)
app.use(express.json());

// Public endpoints (no authentication)
app.get('/api/v1/public/markets', (req, res) => {
  res.json({ markets: ['BTC-USD', 'ETH-USD'] });
});

app.get('/api/v1/public/ticker', (req, res) => {
  res.json({ symbol: 'BTC-USD', price: '50000', volume: '1000' });
});

// Protected endpoints (API key required)
// Note: In production, add rate limiting middleware here
// Example: app.use('/api/v1/private', rateLimiter.private, apiKeyAuth);
app.use('/api/v1/private', apiKeyAuth);

// Account endpoints (read-only)
app.get('/api/v1/private/balance', requireScopes('account:read'), (req, res) => {
  const userId = req.apiKey!.userId;
  res.json({
    userId,
    balances: [
      { asset: 'BTC', available: '1.5', locked: '0.0' },
      { asset: 'USD', available: '10000', locked: '0.0' },
    ],
  });
});

// Trading endpoints (write operations)
app.post(
  '/api/v1/private/orders',
  requireScopes('trading:write'),
  (req, res) => {
    const userId = req.apiKey!.userId;
    const { symbol, side, amount, price } = req.body;

    // Validate and create order
    res.json({
      orderId: 'order-123',
      userId,
      symbol,
      side,
      amount,
      price,
      status: 'pending',
    });
  }
);

app.delete(
  '/api/v1/private/orders/:orderId',
  requireScopes('trading:write'),
  (req, res) => {
    const userId = req.apiKey!.userId;
    const { orderId } = req.params;

    // Cancel order
    res.json({
      orderId,
      userId,
      status: 'cancelled',
    });
  }
);

// Admin endpoints (special privileges)
app.get(
  '/api/v1/private/admin/users',
  requireScopes('admin:read'),
  (req, res) => {
    res.json({
      users: [
        { id: 'user-1', email: 'user1@example.com' },
        { id: 'user-2', email: 'user2@example.com' },
      ],
    });
  }
);

// Error handling
app.use((error: any, req: any, res: any, next: any) => {
  if (error.statusCode) {
    return res.status(error.statusCode).json(error.toJSON(req.id));
  }
  
  logger.error({ error }, 'Unhandled error');
  res.status(500).json({
    error: {
      code: 'INTERNAL_ERROR',
      message: 'Internal server error',
    },
  });
});

// Start server
const PORT = config.HTTP_PORT || 3000;
app.listen(PORT, () => {
  logger.info({ port: PORT }, 'Server started');
});

/**
 * Example Client Request
 * 
 * const crypto = require('crypto');
 * const { v4: uuidv4 } = require('uuid');
 * 
 * const apiKeyId = 'ak_abc123def456';
 * const apiSecret = 'sk_xyz789uvw012';
 * 
 * const timestamp = Date.now().toString();
 * const nonce = uuidv4();
 * const method = 'POST';
 * const path = '/api/v1/private/orders';
 * const query = '';
 * const body = JSON.stringify({
 *   symbol: 'BTC-USD',
 *   side: 'buy',
 *   amount: '1.5',
 *   price: '50000'
 * });
 * 
 * const bodyHash = crypto.createHash('sha256').update(body).digest('hex');
 * const prehash = [timestamp, nonce, method, path, query, bodyHash].join('\n');
 * const signature = crypto
 *   .createHmac('sha256', apiSecret)
 *   .update(prehash)
 *   .digest('base64');
 * 
 * fetch('http://localhost:3000/api/v1/private/orders', {
 *   method: 'POST',
 *   headers: {
 *     'Content-Type': 'application/json',
 *     'X-API-KEY': apiKeyId,
 *     'X-API-TIMESTAMP': timestamp,
 *     'X-API-NONCE': nonce,
 *     'X-API-SIGNATURE': signature,
 *   },
 *   body,
 * });
 */
