/**
 * Order Lock Handler
 * 
 * Handles locking of user funds when orders are placed.
 * This reserves funds in the LOCKED account so they can't be double-spent.
 * 
 * TODO: Implement full logic
 */

import type { PoolClient } from "pg";
import type { OrderEvent, Market } from "../../domain/types.js";

/**
 * Handle an order lock event (when order is ACCEPTED)
 * 
 * @param client - Database client (must be in a transaction)
 * @param orderEvent - Order event from matching engine
 * @param market - Market configuration
 * @returns Success indicator and optional error message
 */
export async function handleOrderLock(
  client: PoolClient,
  orderEvent: OrderEvent,
  market: Market
): Promise<{ ok: boolean; error?: string }> {
  console.log("[order_lock] Not implemented yet", {
    orderId: orderEvent.orderId,
    userId: orderEvent.userId,
    side: orderEvent.side,
    marketId: orderEvent.marketId
  });

  // TODO: Implement order locking logic:
  // 1. Determine which asset to lock based on side
  //    - BUY: lock quote asset (size * price)
  //    - SELL: lock base asset (size)
  // 2. Create ledger transaction with type "TRANSFER"
  // 3. Add entries:
  //    - DEBIT USER:AVAILABLE
  //    - CREDIT USER:LOCKED
  // 4. Update balances cache
  // 5. Create order_locks record

  return { ok: true };
}
