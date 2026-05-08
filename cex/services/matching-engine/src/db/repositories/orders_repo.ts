/**
 * Repository for orders
 */

import type { Pool, PoolClient } from "pg";
import { v4 as uuidv4 } from "uuid";
import { atomsToDecimal, decimalToAtoms } from "../../engine/deterministic.js";
import type { Order, OrderSide, OrderType, TimeInForce, OrderStatus } from "../../engine/types.js";

export class OrdersRepo {
  constructor(private client: PoolClient, private baseDecimals = 8) {}

  /**
   * Create a new order (NEW -> ACCEPTED)
   */
  async createOrder(order: {
    userId: string;
    clientOrderId: string;
    marketId: string;
    side: OrderSide;
    orderType: OrderType;
    timeInForce: TimeInForce;
    priceAtoms: bigint;
    sizeAtoms: bigint;
    postOnly: boolean;
    acceptedAt: Date;
    replaceOf?: string;
  }): Promise<Order> {
    const orderId = uuidv4();
    const price = atomsToDecimal(order.priceAtoms, 8);
    const size = atomsToDecimal(order.sizeAtoms, this.baseDecimals);

    await this.client.query(
      `INSERT INTO orders (
        id, user_id, client_order_id, market_id, market,
        side, order_type, time_in_force, price, quantity,
        filled_quantity, remaining_quantity, post_only,
        status, accepted_at, replace_of
      ) VALUES (
        $1, $2, $3, $4, (SELECT symbol FROM markets WHERE id = $4),
        $5, $6, $7, $8, $9,
        0, $9, $10,
        'ACCEPTED', $11, $12
      )`,
      [
        orderId,
        order.userId,
        order.clientOrderId,
        order.marketId,
        order.side,
        order.orderType,
        order.timeInForce,
        price,
        size,
        order.postOnly,
        order.acceptedAt,
        order.replaceOf || null
      ]
    );

    return {
      id: orderId,
      userId: order.userId,
      clientOrderId: order.clientOrderId,
      marketId: order.marketId,
      side: order.side,
      orderType: order.orderType,
      timeInForce: order.timeInForce,
      priceAtoms: order.priceAtoms,
      sizeAtoms: order.sizeAtoms,
      filledAtoms: 0n,
      remainingAtoms: order.sizeAtoms,
      postOnly: order.postOnly,
      status: "ACCEPTED",
      acceptedAt: order.acceptedAt,
      replaceOf: order.replaceOf
    };
  }

  /**
   * Update order after fill
   */
  async updateOrderFill(
    orderId: string,
    filledAtoms: bigint,
    remainingAtoms: bigint,
    status: OrderStatus
  ): Promise<void> {
    const filled = atomsToDecimal(filledAtoms, this.baseDecimals);
    const remaining = atomsToDecimal(remainingAtoms, this.baseDecimals);
    const completedAt =
      status === "FILLED" ||
      status === "CANCELED" ||
      status === "REJECTED" ||
      status === "EXPIRED" ||
      status === "CANCELED_REPLACED"
        ? new Date()
        : null;

    await this.client.query(
      `UPDATE orders
       SET filled_quantity = $1,
           remaining_quantity = $2,
           status = $3,
           completed_at = $4
       WHERE id = $5`,
      [filled, remaining, status, completedAt, orderId]
    );
  }

  /**
   * Cancel an order
   */
  async cancelOrder(orderId: string, reason?: string): Promise<void> {
    await this.client.query(
      `UPDATE orders
       SET status = 'CANCELED',
           completed_at = NOW(),
           reject_reason = $2
       WHERE id = $1`,
      [orderId, reason || null]
    );
  }

  /**
   * Mark order as replaced
   */
  async markReplaced(orderId: string): Promise<void> {
    await this.client.query(
      `UPDATE orders
       SET status = 'CANCELED_REPLACED',
           completed_at = NOW()
       WHERE id = $1`,
      [orderId]
    );
  }

  /**
   * Reject an order
   */
  async rejectOrder(orderId: string, reason: string): Promise<void> {
    await this.client.query(
      `UPDATE orders
       SET status = 'REJECTED',
           completed_at = NOW(),
           reject_reason = $2
       WHERE id = $1`,
      [orderId, reason]
    );
  }

  /**
   * Get order by ID
   */
  async getById(orderId: string): Promise<Order | null> {
    const result = await this.client.query(
      `SELECT * FROM orders WHERE id = $1`,
      [orderId]
    );

    if (result.rows.length === 0) return null;
    return this.rowToOrder(result.rows[0]);
  }

  /**
   * Get open orders for a market (for recovery)
   * Sorted by accepted_at, then id for deterministic replay
   */
  async getOpenOrdersByMarket(marketId: string): Promise<Order[]> {
    const result = await this.client.query(
      `SELECT * FROM orders
       WHERE market_id = $1
         AND status IN ('ACCEPTED', 'PARTIAL_FILL')
         AND remaining_quantity > 0
       ORDER BY accepted_at ASC, id ASC`,
      [marketId]
    );

    return result.rows.map((row) => this.rowToOrder(row));
  }

  /**
   * Get order by user and client_order_id
   */
  async getByClientOrderId(userId: string, clientOrderId: string): Promise<Order | null> {
    const result = await this.client.query(
      `SELECT * FROM orders
       WHERE user_id = $1 AND client_order_id = $2
       ORDER BY created_at DESC
       LIMIT 1`,
      [userId, clientOrderId]
    );

    if (result.rows.length === 0) return null;
    return this.rowToOrder(result.rows[0]);
  }

  private rowToOrder(row: Record<string, any>): any {
    return {
      id: row.id as string,
      userId: row.user_id as string,
      clientOrderId: row.client_order_id as string,
      marketId: row.market_id as string,
      side: row.side as OrderSide,
      orderType: row.order_type as OrderType,
      timeInForce: row.time_in_force as TimeInForce,
      priceAtoms: decimalToAtoms(row.price as string, 8),
      sizeAtoms: decimalToAtoms(row.quantity as string, this.baseDecimals),
      filledAtoms: decimalToAtoms(row.filled_quantity as string, this.baseDecimals),
      remainingAtoms: decimalToAtoms(row.remaining_quantity as string, this.baseDecimals),
      postOnly: row.post_only as boolean,
      status: row.status as OrderStatus,
      acceptedAt: new Date(row.accepted_at as string),
      replaceOf: (row.replace_of as string) || undefined
    };
  }
}
