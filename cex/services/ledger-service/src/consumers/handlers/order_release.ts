/**
 * Order Release Handler
 *
 * Handles releasing locked funds on cancel/expire/reject/filled remainder.
 */

import type { PoolClient } from "pg";
import type { OrderEvent, Market } from "../../domain/types.js";
import { AccountsRepo, LedgerRepo, BalancesRepo } from "../../db/repositories/index.js";

export async function handleOrderRelease(
  client: PoolClient,
  orderEvent: OrderEvent,
  market: Market
): Promise<{ ok: boolean; error?: string }> {
  try {
    const accountsRepo = new AccountsRepo(client);
    const ledgerRepo = new LedgerRepo(client);
    const balancesRepo = new BalancesRepo(client);

    const res = await client.query(
      `SELECT order_id, user_id, asset_id, locked_atoms, used_atoms
         FROM order_locks
        WHERE order_id = $1
        FOR UPDATE`,
      [orderEvent.orderId]
    );

    if ((res.rowCount ?? 0) === 0) return { ok: true };

    const row = res.rows[0];
    const assetId = String(row.asset_id);
    const lockedAtoms = BigInt(row.locked_atoms);
    const usedAtoms = BigInt(row.used_atoms);
    const releasableAtoms = lockedAtoms - usedAtoms;

    if (releasableAtoms <= 0n) {
      await client.query(`DELETE FROM order_locks WHERE order_id = $1`, [orderEvent.orderId]);
      return { ok: true };
    }

    const accounts = await accountsRepo.ensureUserAccounts(orderEvent.userId, assetId);
    const balance = await balancesRepo.getBalance(orderEvent.userId, assetId);
    const availableAtoms = balance?.availableAtoms ?? 0n;
    const currentLockedAtoms = balance?.lockedAtoms ?? 0n;

    if (currentLockedAtoms < releasableAtoms) {
      return {
        ok: false,
        error: `insufficient_locked_balance:${assetId}:${currentLockedAtoms.toString()}<${releasableAtoms.toString()}`
      };
    }

    const tx = await ledgerRepo.createTransaction(
      "TRANSFER",
      market.id,
      BigInt(orderEvent.sequence),
      {
        reason: "ORDER_RELEASE",
        orderId: orderEvent.orderId,
        eventType: orderEvent.eventType,
        userId: orderEvent.userId,
        assetId,
        releasableAtoms: releasableAtoms.toString(),
      }
    );

    await ledgerRepo.addEntry(
      tx.id,
      accounts.available.id,
      assetId,
      "DEBIT",
      releasableAtoms,
      `Order ${orderEvent.orderId} release -> available`
    );

    await ledgerRepo.addEntry(
      tx.id,
      accounts.locked.id,
      assetId,
      "CREDIT",
      releasableAtoms,
      `Order ${orderEvent.orderId} release -> locked`
    );

    await balancesRepo.updateBalance(
      orderEvent.userId,
      assetId,
      availableAtoms + releasableAtoms,
      currentLockedAtoms - releasableAtoms
    );

    await client.query(`DELETE FROM order_locks WHERE order_id = $1`, [orderEvent.orderId]);

    return { ok: true };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
