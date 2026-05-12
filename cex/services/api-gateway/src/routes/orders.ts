import { Router } from "express";
import { Pool } from "pg";
import { NatsConnection } from "nats";
import { z } from "zod";
import { randomUUID } from "node:crypto";
import { jsonCodec, subjects } from "@cex/common";
import {
  createApiKeyVerifier,
  createRequireAuth,
  requireApiKeyScope,
} from "./authenticated.js";
import {
  processReferralQualificationInTransaction,
  type ReferralProcessingOptions,
} from "./referrals.js";


const router = Router();

// Validation schemas
const createOrderSchema = z.object({
  symbol: z.string(),
  side: z.enum(["buy", "sell"]),
  type: z.enum(["LIMIT", "MARKET", "POST_ONLY", "IOC", "FOK"]).default("LIMIT"),
  price: z.number().optional(),
  quantity: z.number().positive(),
  clientOrderId: z.string().optional(),
  idempotencyKey: z.string().optional(),
});

export function createOrdersRouter(
  pgPool: Pool,
  nats: NatsConnection,
  authServiceUrl: string,
  referralOptions: ReferralProcessingOptions = {}
): any {
  const requireAuth = createRequireAuth(authServiceUrl, {
    verifyApiKey: createApiKeyVerifier(pgPool),
  });
  const requireReadScope = requireApiKeyScope("read");
  const requireTradeScope = requireApiKeyScope("trade");
  /**
   * POST /orders - Create a new order
   */
  router.post("/orders", requireAuth, requireTradeScope, async (req: any, res) => {
    try {
      const body = createOrderSchema.parse(req.body);
      const userId = req.userId;
      const clientOrderId = body.clientOrderId || randomUUID();
      const idempotencyKey = body.idempotencyKey || `order-${userId}-${clientOrderId}-${Date.now()}`;

      // Check idempotency
      if (body.idempotencyKey) {
        const idempotencyResult = await pgPool.query(
          "SELECT result FROM idempotency_keys WHERE key = $1 AND consumer = 'api-gateway'",
          [idempotencyKey]
        );

        if (idempotencyResult.rows.length > 0) {
          // Return cached result
          return res.json(idempotencyResult.rows[0].result);
        }
      }

      // Validate market exists
      const marketResult = await pgPool.query(
        "SELECT id, price_tick, size_step, min_order_size FROM markets WHERE symbol = $1 AND active = true",
        [body.symbol]
      );

      if (marketResult.rows.length === 0) {
        return res.status(404).json({ error: "Market not found" });
      }

      const market = marketResult.rows[0];

      // Validate price tick and size step
      if (body.type && body.price) {
        const priceTick = parseFloat(market.price_tick);
        // Use epsilon comparison for floating-point precision
        const priceRemainder = body.price % priceTick;
        const epsilon = priceTick / 1000;
        if (Math.abs(priceRemainder) > epsilon && Math.abs(priceRemainder - priceTick) > epsilon) {
          return res.status(400).json({
            error: "Invalid price",
            message: `Price must be a multiple of ${priceTick}`,
          });
        }
      }

      const sizeStep = parseFloat(market.size_step);
      // Use epsilon comparison for floating-point precision
      const qtyRemainder = body.quantity % sizeStep;
      const epsilon = sizeStep / 1000;
      if (Math.abs(qtyRemainder) > epsilon && Math.abs(qtyRemainder - sizeStep) > epsilon) {
        return res.status(400).json({
          error: "Invalid quantity",
          message: `Quantity must be a multiple of ${sizeStep}`,
        });
      }

      const minOrderSize = parseFloat(market.min_order_size);
      if (body.quantity < minOrderSize) {
        return res.status(400).json({
          error: "Order too small",
          message: `Minimum order size is ${minOrderSize}`,
        });
      }

      // Publish order to NATS
      const orderCommand = {
        event_id: randomUUID(),
        correlation_id: randomUUID(),
        causation_id: randomUUID(),
        created_at: new Date().toISOString(),
        idempotency_key: idempotencyKey,
        type: "OrderSubmit",
        user_id: userId,
        client_order_id: clientOrderId,
        market: body.symbol,
        side: body.side,
        order_type: body.type,
        price: body.price || 0,
        quantity: body.quantity,
      };

      nats.publish(subjects.orderSubmit, jsonCodec.encode(orderCommand));

      processReferralQualificationInTransaction(pgPool, userId, "order_submitted", referralOptions).catch((error) => {
        console.error("Error processing referral qualification for order:", error);
      });

      // Create response
      const response = {
        orderId: orderCommand.event_id, // Temp ID until order is accepted
        clientOrderId,
        symbol: body.symbol,
        side: body.side,
        type: body.type,
        price: body.price,
        quantity: body.quantity,
        status: "pending",
        message: "Order submitted for processing",
      };

      // Store idempotency key
      if (body.idempotencyKey) {
        await pgPool.query(
          `INSERT INTO idempotency_keys (key, consumer, result, created_at, expires_at)
           VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '24 hours')
           ON CONFLICT (key) DO NOTHING`,
          [idempotencyKey, "api-gateway", JSON.stringify(response)]
        );
      }

      res.status(202).json(response);
    } catch (error: any) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: "Invalid request", details: error.errors });
      }
      console.error("Error creating order:", error);
      res.status(500).json({ error: "Failed to create order" });
    }
  });

  /**
   * DELETE /orders/:id - Cancel an order
   */
  router.delete("/orders/:id", requireAuth, requireTradeScope, async (req: any, res) => {
    try {
      const orderId = req.params.id;
      const userId = req.userId;

      // Verify order belongs to user
      const orderResult = await pgPool.query(
        "SELECT id, market, status FROM orders WHERE id = $1 AND user_id = $2",
        [orderId, userId]
      );

      if (orderResult.rows.length === 0) {
        return res.status(404).json({ error: "Order not found" });
      }

      const order = orderResult.rows[0];

      if (order.status !== "ACCEPTED" && order.status !== "PARTIAL_FILL") {
        return res.status(400).json({ error: "Order cannot be cancelled", status: order.status });
      }

      // Publish cancel command
      const cancelCommand = {
        event_id: randomUUID(),
        correlation_id: randomUUID(),
        causation_id: randomUUID(),
        created_at: new Date().toISOString(),
        type: "OrderCancel",
        user_id: userId,
        order_id: orderId,
        market: order.market,
      };

      nats.publish(subjects.orderCancel, jsonCodec.encode(cancelCommand));

      res.json({
        orderId,
        status: "cancelling",
        message: "Order cancel request submitted",
      });
    } catch (error) {
      console.error("Error cancelling order:", error);
      res.status(500).json({ error: "Failed to cancel order" });
    }
  });

  /**
   * GET /me/orders - Get user's orders
   */
  router.get("/me/orders", requireAuth, requireReadScope, async (req: any, res) => {
    try {
      res.set("Cache-Control", "no-store");
      const userId = req.userId;
      const symbol = req.query.symbol as string | undefined;
      const status = req.query.status as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);

      let query = `
        SELECT 
          o.id,
          o.client_order_id,
          o.market as symbol,
          o.side,
          o.order_type as type,
          o.price,
          o.quantity,
          o.filled_quantity,
          o.remaining_quantity,
          o.post_only,
          o.status,
          o.created_at,
          o.accepted_at,
          o.completed_at
        FROM orders o
        WHERE o.user_id = $1
      `;

      const params: any[] = [userId];
      let paramIndex = 2;

      if (symbol) {
        query += ` AND o.market = $${paramIndex}`;
        params.push(symbol);
        paramIndex++;
      }

      if (status) {
        query += ` AND o.status = $${paramIndex}`;
        params.push(status);
        paramIndex++;
      }

      query += ` ORDER BY o.created_at DESC LIMIT $${paramIndex}`;
      params.push(limit);

      const result = await pgPool.query(query, params);

      res.json({
        orders: result.rows.map((row: any) => ({
          id: row.id,
          clientOrderId: row.client_order_id,
          symbol: row.symbol,
          side: String(row.side).toLowerCase(),
          type: row.post_only ? "POST_ONLY" : row.type,
          price: row.price ? parseFloat(row.price) : undefined,
          quantity: parseFloat(row.quantity),
          filledQuantity: parseFloat(row.filled_quantity),
          remainingQuantity: row.remaining_quantity ? parseFloat(row.remaining_quantity) : undefined,
          status: row.status,
          createdAt: new Date(row.created_at).getTime(),
          acceptedAt: row.accepted_at ? new Date(row.accepted_at).getTime() : undefined,
          completedAt: row.completed_at ? new Date(row.completed_at).getTime() : undefined,
        })),
      });
    } catch (error) {
      console.error("Error fetching orders:", error);
      res.status(500).json({ error: "Failed to fetch orders" });
    }
  });

  /**
   * GET /me/trades - Get user's trade history
   */
  router.get("/me/trades", requireAuth, requireReadScope, async (req: any, res) => {
    try {
      const userId = req.userId;
      const symbol = req.query.symbol as string | undefined;
      const limit = Math.min(parseInt(req.query.limit as string) || 100, 500);

      let query = `
        SELECT 
          t.id,
          t.price,
          t.size as quantity,
          t.maker_fee,
          t.taker_fee,
          t.fee_asset,
          t.created_at,
          m.symbol,
          o.side,
          o.id as order_id,
          CASE 
            WHEN o.id = t.maker_order_id THEN 'maker'
            ELSE 'taker'
          END as role
        FROM trades t
        JOIN orders o ON (t.maker_order_id = o.id OR t.taker_order_id = o.id)
        JOIN markets m ON t.market_id = m.id
        WHERE o.user_id = $1
      `;

      const params: any[] = [userId];
      let paramIndex = 2;

      if (symbol) {
        query += ` AND m.symbol = $${paramIndex}`;
        params.push(symbol);
        paramIndex++;
      }

      query += ` ORDER BY t.created_at DESC LIMIT $${paramIndex}`;
      params.push(limit);

      const result = await pgPool.query(query, params);

      res.json({
        trades: result.rows.map((row: any) => ({
          id: row.id,
          orderId: row.order_id,
          symbol: row.symbol,
          side: String(row.side).toLowerCase(),
          price: parseFloat(row.price),
          quantity: parseFloat(row.quantity),
          fee: row.role === "maker" ? parseFloat(row.maker_fee) : parseFloat(row.taker_fee),
          feeAsset: row.fee_asset,
          role: row.role,
          timestamp: new Date(row.created_at).getTime(),
        })),
      });
    } catch (error) {
      console.error("Error fetching trades:", error);
      res.status(500).json({ error: "Failed to fetch trades" });
    }
  });

  /**
   * GET /me/balances - Get user's balances
   */
  router.get("/me/balances", requireAuth, requireReadScope, async (req: any, res) => {
    try {
      res.set("Cache-Control", "no-store");
      const userId = req.userId;

      const result = await pgPool.query(
        `
        SELECT
          asset,
          COALESCE(available, 0) AS available,
          COALESCE(locked, 0) AS locked
        FROM balances
        WHERE account_id = $1
          AND (COALESCE(available, 0) > 0 OR COALESCE(locked, 0) > 0)
        ORDER BY asset
      `,
        [`user:${userId}`]
      );

      res.json({
        balances: result.rows.map((row: any) => ({
          asset: row.asset,
          available: parseFloat(row.available),
          locked: parseFloat(row.locked),
          total: parseFloat(row.available) + parseFloat(row.locked),
        })),
      });
    } catch (error) {
      console.error("Error fetching balances:", error);
      res.status(500).json({ error: "Failed to fetch balances" });
    }
  });

  return router;
}
