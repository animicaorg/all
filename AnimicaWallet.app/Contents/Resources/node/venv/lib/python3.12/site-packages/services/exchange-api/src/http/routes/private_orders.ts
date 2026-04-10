/**
 * Private Order Management Endpoints
 * Requires API key authentication with order permissions
 */

import { Router } from 'express';
import { z } from 'zod';
import type { PrismaClient, OrderStatus as PrismaOrderStatus } from '@prisma/client';
import type { Logger } from '../../utils/logger.js';
import type { Config } from '../../config.js';
import { MatchingEngineClient, OrderSide, OrderType, OrderStatus } from '../../services/matching_engine_client.js';
import { MarketsRepository } from '../../db/repositories/markets_repo.js';
import { AuditRepository } from '../../db/repositories/audit_repo.js';
import { validate, type ValidatedRequest } from '../middleware/validation.js';
import type { ApiKeyAuthRequest } from '../middleware/api_key_auth.js';
import { requireScopes } from '../middleware/api_key_auth.js';
import {
  createPaginationSchema,
  createPaginationResponse,
  encodeCursor,
  decodeCursor,
} from '../middleware/pagination.js';
import { NotFoundError, ValidationError, ConflictError, BadRequestError } from '../../utils/errors.js';
import { Decimal } from '@prisma/client/runtime/library';

interface AuthenticatedRequest extends ApiKeyAuthRequest, ValidatedRequest {
  apiKey: {
    id: string;
    userId: string;
    scopes: string[];
  };
}

/**
 * Validation schemas
 */
const listOrdersQuerySchema = (config: Config) =>
  z
    .object({
      market: z.string().optional(),
      status: z.enum(['OPEN', 'PARTIALLY_FILLED', 'FILLED', 'CANCELED', 'REJECTED', 'EXPIRED']).optional(),
    })
    .merge(createPaginationSchema(config));

const placeOrderSchema = z.object({
  market: z.string().min(1),
  side: z.enum(['BUY', 'SELL']),
  type: z.enum(['LIMIT', 'MARKET']),
  price: z.string().optional(),
  size: z.string().min(1),
  client_order_id: z.string().optional(),
  time_in_force: z.enum(['GTC', 'IOC', 'FOK']).optional().default('GTC'),
});

const cancelOrderParamsSchema = z.object({
  id: z.string().uuid(),
});

const replaceOrderParamsSchema = z.object({
  id: z.string().uuid(),
});

const replaceOrderBodySchema = z.object({
  price: z.string().optional(),
  size: z.string().optional(),
  client_order_id: z.string().optional(),
});

interface OrderResponse {
  order_id: string;
  market: string;
  side: string;
  type: string;
  status: string;
  price?: string;
  size: string;
  filled_size: string;
  remaining_size: string;
  avg_fill_price: string;
  client_order_id?: string;
  created_at: string;
  updated_at: string;
}

interface PlaceOrderResponse {
  order_id: string;
  status: string;
  message?: string;
}

/**
 * Validate order against market rules
 */
async function validateOrderAgainstMarket(
  marketsRepo: MarketsRepository,
  marketSymbol: string,
  price: string | undefined,
  size: string,
  orderType: string
): Promise<void> {
  const market = await marketsRepo.getBySymbol(marketSymbol);

  if (!market) {
    throw new NotFoundError('Market not found');
  }

  if (market.status !== 'ONLINE') {
    throw new BadRequestError('Market is not available for trading');
  }

  const sizeDecimal = new Decimal(size);
  const minOrderSize = market.minOrderSize;
  const sizeStep = market.sizeStep;

  // Check minimum order size
  if (sizeDecimal.lessThan(minOrderSize)) {
    throw new ValidationError('Order size below minimum', {
      min_order_size: minOrderSize.toString(),
      provided: size,
    });
  }

  // Check size step
  const sizeRemainder = sizeDecimal.mod(sizeStep);
  if (!sizeRemainder.isZero()) {
    throw new ValidationError('Order size does not match size step', {
      size_step: sizeStep.toString(),
      provided: size,
    });
  }

  // Validate price for limit orders
  if (orderType === 'LIMIT' && price) {
    const priceDecimal = new Decimal(price);
    const priceTick = market.priceTick;

    if (priceDecimal.lessThanOrEqualTo(0)) {
      throw new ValidationError('Price must be positive');
    }

    // Check price tick
    const priceRemainder = priceDecimal.mod(priceTick);
    if (!priceRemainder.isZero()) {
      throw new ValidationError('Order price does not match price tick', {
        price_tick: priceTick.toString(),
        provided: price,
      });
    }
  }
}

/**
 * Create private orders router
 */
export function createPrivateOrdersRouter(
  prisma: PrismaClient,
  matchingEngineClient: MatchingEngineClient,
  config: Config,
  logger: Logger
): Router {
  const router = Router();
  const marketsRepo = new MarketsRepository(prisma);
  const auditRepo = new AuditRepository(prisma);

  /**
   * GET /api/v1/orders
   * List user orders with filters and cursor pagination
   */
  router.get(
    '/',
    requireScopes('orders:read'),
    validate({ query: listOrdersQuerySchema(config) }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { market, status, limit, cursor } = req.validated.query;

        // Build where clause
        const where: any = { userId };

        if (market) {
          const marketRecord = await marketsRepo.getBySymbol(market);
          if (!marketRecord) {
            throw new NotFoundError('Market not found');
          }
          where.marketId = marketRecord.id;
        }

        if (status) {
          where.status = status;
        }

        // Handle cursor pagination
        if (cursor) {
          try {
            const cursorData = decodeCursor(cursor);
            where.createdAt = { lt: new Date(cursorData.created_at as string) };
          } catch (error) {
            throw new ValidationError('Invalid cursor');
          }
        }

        // Fetch orders (fetch limit + 1 to check if there are more)
        const orders = await prisma.order.findMany({
          where,
          include: {
            market: {
              select: { symbol: true },
            },
          },
          orderBy: { createdAt: 'desc' },
          take: limit + 1,
        });

        // Transform to response format
        const orderResponses: OrderResponse[] = orders.slice(0, limit).map((order) => ({
          order_id: order.id,
          market: order.market.symbol,
          side: order.side,
          type: order.type,
          status: order.status,
          price: order.price?.toString(),
          size: order.size.toString(),
          filled_size: order.filledSize.toString(),
          remaining_size: order.remainingSize.toString(),
          avg_fill_price: order.avgFillPrice.toString(),
          client_order_id: order.clientOrderId || undefined,
          created_at: order.createdAt.toISOString(),
          updated_at: order.updatedAt.toISOString(),
        }));

        // Create paginated response
        const response = createPaginationResponse(
          orderResponses,
          limit,
          (order) => encodeCursor({ created_at: order.created_at })
        );

        logger.debug({ userId, count: orderResponses.length }, 'Orders retrieved');
        res.json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to list orders');
        next(error);
      }
    }
  );

  /**
   * POST /api/v1/orders
   * Place a new order (limit or market)
   */
  router.post(
    '/',
    requireScopes('orders:write'),
    validate({ body: placeOrderSchema }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const { market, side, type, price, size, client_order_id, time_in_force } = req.validated.body;

        // Validate market order must not have price
        if (type === 'MARKET' && price) {
          throw new ValidationError('Market orders cannot have a price');
        }

        // Validate limit order must have price
        if (type === 'LIMIT' && !price) {
          throw new ValidationError('Limit orders must have a price');
        }

        // Validate against market rules
        await validateOrderAgainstMarket(marketsRepo, market, price, size, type);

        // Check idempotency
        const idempotencyKey = req.headers['idempotency-key'] as string | undefined;
        if (idempotencyKey) {
          // Check if we already processed this request
          const existingOrder = await prisma.order.findFirst({
            where: {
              userId,
              clientOrderId: idempotencyKey,
            },
          });

          if (existingOrder) {
            logger.info({ userId, orderId: existingOrder.id }, 'Idempotent order submission');
            return res.status(200).json({
              order_id: existingOrder.id,
              status: existingOrder.status,
              message: 'Order already exists',
            });
          }
        }

        // Check client_order_id uniqueness if provided
        if (client_order_id) {
          const marketRecord = await marketsRepo.getBySymbol(market);
          const existingOrder = await prisma.order.findFirst({
            where: {
              userId,
              marketId: marketRecord!.id,
              clientOrderId: client_order_id,
            },
          });

          if (existingOrder) {
            throw new ConflictError('Client order ID already exists', {
              order_id: existingOrder.id,
            });
          }
        }

        // Submit order to matching engine
        const orderResponse = await matchingEngineClient.submitOrder({
          userId,
          market,
          side: side as OrderSide,
          type: type as OrderType,
          price,
          quantity: size,
          clientOrderId: client_order_id || idempotencyKey,
          timeInForce: time_in_force,
        });

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'ORDER_PLACED',
          entityType: 'ORDER',
          entityId: orderResponse.orderId,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          after: {
            market,
            side,
            type,
            price,
            size,
            client_order_id,
          },
        });

        const response: PlaceOrderResponse = {
          order_id: orderResponse.orderId,
          status: orderResponse.status,
          message: orderResponse.message,
        };

        logger.info({ userId, orderId: orderResponse.orderId }, 'Order placed');
        res.status(201).json(response);
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId }, 'Failed to place order');
        next(error);
      }
    }
  );

  /**
   * DELETE /api/v1/orders/:id
   * Cancel an order
   */
  router.delete(
    '/:id',
    requireScopes('orders:write'),
    validate({ params: cancelOrderParamsSchema }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const orderId = req.validated.params.id;

        // Verify order belongs to user
        const order = await prisma.order.findUnique({
          where: { id: orderId },
          include: {
            market: {
              select: { symbol: true },
            },
          },
        });

        if (!order) {
          throw new NotFoundError('Order not found');
        }

        if (order.userId !== userId) {
          throw new NotFoundError('Order not found');
        }

        // Check if order can be canceled
        if (!['OPEN', 'PARTIALLY_FILLED'].includes(order.status)) {
          throw new BadRequestError('Order cannot be canceled', {
            status: order.status,
          });
        }

        // Cancel order via matching engine
        const cancelResponse = await matchingEngineClient.cancelOrder({
          orderId: order.id,
          userId,
          market: order.market.symbol,
        });

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'ORDER_CANCELED',
          entityType: 'ORDER',
          entityId: orderId,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          before: { status: order.status },
          after: { status: cancelResponse.status },
        });

        logger.info({ userId, orderId }, 'Order canceled');
        res.json({
          order_id: cancelResponse.orderId,
          status: cancelResponse.status,
          message: cancelResponse.message,
        });
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId, orderId: req.params.id }, 'Failed to cancel order');
        next(error);
      }
    }
  );

  /**
   * POST /api/v1/orders/:id/replace
   * Cancel and replace an order with new parameters
   */
  router.post(
    '/:id/replace',
    requireScopes('orders:write'),
    validate({
      params: replaceOrderParamsSchema,
      body: replaceOrderBodySchema,
    }),
    async (req: AuthenticatedRequest, res, next) => {
      try {
        const userId = req.apiKey.userId;
        const orderId = req.validated.params.id;
        const { price, size, client_order_id } = req.validated.body;

        // Verify order belongs to user
        const order = await prisma.order.findUnique({
          where: { id: orderId },
          include: {
            market: {
              select: { symbol: true },
            },
          },
        });

        if (!order) {
          throw new NotFoundError('Order not found');
        }

        if (order.userId !== userId) {
          throw new NotFoundError('Order not found');
        }

        // Check if order can be replaced
        if (!['OPEN', 'PARTIALLY_FILLED'].includes(order.status)) {
          throw new BadRequestError('Order cannot be replaced', {
            status: order.status,
          });
        }

        // Use existing values if not provided
        const newPrice = price || order.price?.toString();
        const newSize = size || order.size.toString();

        // Validate new parameters
        await validateOrderAgainstMarket(
          marketsRepo,
          order.market.symbol,
          newPrice,
          newSize,
          order.type
        );

        // Cancel existing order
        await matchingEngineClient.cancelOrder({
          orderId: order.id,
          userId,
          market: order.market.symbol,
        });

        // Place new order
        const newOrderResponse = await matchingEngineClient.submitOrder({
          userId,
          market: order.market.symbol,
          side: order.side as OrderSide,
          type: order.type as OrderType,
          price: newPrice,
          quantity: newSize,
          clientOrderId: client_order_id,
          timeInForce: order.timeInForce,
        });

        // Audit log
        await auditRepo.log({
          actorUserId: userId,
          actorType: 'USER',
          action: 'ORDER_REPLACED',
          entityType: 'ORDER',
          entityId: orderId,
          ip: req.ip,
          userAgent: req.headers['user-agent'],
          before: {
            price: order.price?.toString(),
            size: order.size.toString(),
          },
          after: {
            new_order_id: newOrderResponse.orderId,
            price: newPrice,
            size: newSize,
          },
        });

        logger.info({ userId, oldOrderId: orderId, newOrderId: newOrderResponse.orderId }, 'Order replaced');
        res.json({
          old_order_id: orderId,
          new_order_id: newOrderResponse.orderId,
          status: newOrderResponse.status,
          message: 'Order replaced successfully',
        });
      } catch (error) {
        logger.error({ error, userId: req.apiKey?.userId, orderId: req.params.id }, 'Failed to replace order');
        next(error);
      }
    }
  );

  return router;
}
