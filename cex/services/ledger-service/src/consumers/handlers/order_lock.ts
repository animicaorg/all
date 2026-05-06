/**
 * Order Lock Handler
 *
 * Handles locking of user funds when orders are placed.
 */

import type { PoolClient } from "pg";
import type { OrderEvent, Market } from "../../domain/types.js";
import { AccountsRepo, LedgerRepo, BalancesRepo } from "../../db/repositories/index.js";
import { getAssetDecimals } from "../../domain/money.js";

function quoteForBuy(baseAtoms: bigint, priceAtoms: bigint, quoteDecimals: number): bigint {
  return (baseAtoms * priceAtoms) / (10n ** BigInt(quoteDecimals));
}

export async function handleOrderLock(
  client: PoolClient,
  orderEvent: OrderEvent,
  market: Market
): Promise<{ ok: boolean; error?: string }> {
  try {
    const accountsRepo = new AccountsRepo(client);
    const ledgerRepo = new LedgerRepo(client);
    const balancesRepo = new BalancesRepo(client);

    const existing = await client.query(
      `SELECT order_id FROM order_locks WHERE order_id = $1`,
      [orderEvent.orderId]
    );
    if ((existing.rowCount ?? 0) > 0) return { ok: true };

    const sizeAtoms = BigInt(orderEvent.sizeAtoms || "0");
    const priceAtoms = BigInt(orderEvent.priceAtoms || "0");
    if (sizeAtoms <= 0n) return { ok: true };

    const assetId = orderEvent.side === "BUY" ? market.quoteAsset : market.baseAsset;
    const lockAmountAtoms =
      orderEvent.side === "BUY"
        ? quoteForBuy(sizeAtoms, priceAtoms, getAssetDecimals(market.quoteAsset))
        : sizeAtoms;

    if (lockAmountAtoms <= 0n) return { ok: true };

    const accounts = await accountsRepo.ensureUserAccounts(orderEvent.userId, assetId);
    const balance = await balancesRepo.getBalance(orderEvent.userId, assetId);
    const availableAtoms = balance?.availableAtoms ?? 0n;
    const lockedAtoms = balance?.lockedAtoms ?? 0n;

    if (availableAtoms < lockAmountAtoms) {
      return {
        ok: false,
        error: `insufficient_available_balance:${assetId}:${availableAtoms.toString()}<${lockAmountAtoms.toString()}`
      };
    }

    const tx = await ledgerRepo.createTransaction(
      "TRANSFER",
      market.id,
      BigInt(orderEvent.sequence),
      {
        reason: "ORDER_LOCK",
        orderId: orderEvent.orderId,
        userId: orderEvent.userId,
        side: orderEvent.side,
        assetId,
        lockAmountAtoms: lockAmountAtoms.toString(),
      }
    );

    // Correct direction: AVAILABLE -> LOCKED
    await ledgerRepo.addEntry(
      tx.id,
      accounts.available.id,
      assetId,
      "DEBIT",
      lockAmountAtoms,
      `Order ${orderEvent.orderId} lock: available -> locked`
    );

    await ledgerRepo.addEntry(
      tx.id,
      accounts.locked.id,
      assetId,
      "CREDIT",
      lockAmountAtoms,
      `Order ${orderEvent.orderId} lock: available -> locked`
    );

    await balancesRepo.updateBalance(
      orderEvent.userId,
      assetId,
      availableAtoms - lockAmountAtoms,
      lockedAtoms + lockAmountAtoms
    );

    await client.query(
      `INSERT INTO order_locks (order_id, user_id, asset_id, locked_atoms, used_atoms, created_at, updated_at)
       VALUES ($1, $2, $3, $4, 0, NOW(), NOW())
       ON CONFLICT (order_id) DO UPDATE
         SET user_id = EXCLUDED.user_id,
             asset_id = EXCLUDED.asset_id,
             locked_atoms = EXCLUDED.locked_atoms,
             updated_at = NOW()`,
      [orderEvent.orderId, orderEvent.userId, assetId, lockAmountAtoms.toString()]
    );

    return { ok: true };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
