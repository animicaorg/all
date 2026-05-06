/**
 * Repository for order events
 */

import type { Pool, PoolClient } from "pg";
import { v4 as uuidv4 } from "uuid";
import type { OrderEvent } from "../../engine/types.js";
import { stringifyJson } from "../../utils/json.js";

export class EventsRepo {
  constructor(private client: PoolClient) {}

  /**
   * Append an order event
   */
  async appendEvent(event: {
    orderId: string;
    marketId: string;
    eventType: string;
    sequence: bigint;
    payload: Record<string, any>;
  }): Promise<OrderEvent> {
    const eventId = uuidv4();
    const createdAt = new Date();

    await this.client.query(
      `INSERT INTO order_events (
        id, order_id, market_id, event_type, sequence, payload, created_at
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7
      )`,
      [
        eventId,
        event.orderId,
        event.marketId,
        event.eventType,
        event.sequence.toString(),
        stringifyJson(event.payload),
        createdAt
      ]
    );

    return {
      id: eventId,
      orderId: event.orderId,
      marketId: event.marketId,
      eventType: event.eventType,
      sequence: event.sequence,
      payload: event.payload,
      createdAt
    };
  }

  /**
   * Get events for an order
   */
  async getEventsByOrderId(orderId: string): Promise<OrderEvent[]> {
    const result = await this.client.query(
      `SELECT * FROM order_events
       WHERE order_id = $1
       ORDER BY created_at ASC`,
      [orderId]
    );

    return result.rows.map((row) => ({
      id: row.id,
      orderId: row.order_id,
      marketId: row.market_id,
      eventType: row.event_type,
      sequence: BigInt(row.sequence),
      payload: row.payload,
      createdAt: new Date(row.created_at)
    }));
  }
}
