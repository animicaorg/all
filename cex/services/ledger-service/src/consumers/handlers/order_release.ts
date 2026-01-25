/**
 * Order Release Handler
 * 
 * Handles releasing of locked funds when orders are:
 * - CANCELED (release all remaining locked funds)
 * - EXPIRED (release all remaining locked funds)
 * - REJECTED (release all locked funds)
 * 
 * Note: FILLED orders are handled by trade settlement, not here.
 * 
 * TODO: Implement full logic
 */

import type { PoolClient } from "pg";
import type { OrderEvent, Market } from "../../domain/types.js";

/**
 * Handle an order release event (CANCELED, EXPIRED, REJECTED)
 * 
 * @param client - Database client (must be in a transaction)
 * @param orderEvent - Order event from matching engine
 * @param market - Market configuration
 * @returns Success indicator and optional error message
 */
export async function handleOrderRelease(
  client: PoolClient,
  orderEvent: OrderEvent,
  market: Market
): Promise<{ ok: boolean; error?: string }> {
  console.log("[order_release] Not implemented yet", {
    orderId: orderEvent.orderId,
    userId: orderEvent.userId,
    eventType: orderEvent.eventType,
    remainingAtoms: orderEvent.remainingAtoms
  });

  // TODO: Implement order release logic:
  // 1. Get order_locks record to find locked amount
  // 2. Calculate amount to release (locked - used)
  // 3. Create ledger transaction with type "TRANSFER"
  // 4. Add entries:
  //    - DEBIT USER:LOCKED
  //    - CREDIT USER:AVAILABLE
  // 5. Update balances cache
  // 6. Update or delete order_locks record

  return { ok: true };
}
