/**
 * Balances Repository
 * Manages balance cache (derived from ledger entries)
 */

import type { PoolClient } from "pg";
import type { Balance, UserBalance } from "../../domain/types.js";

export class BalancesRepo {
  constructor(private client: PoolClient) {}

  /**
   * Get cached balance for an account
   */
  async getBalance(accountId: string, assetId: string): Promise<Balance | null> {
    const result = await this.client.query(
      `SELECT user_id as account_id, asset_id, available_atoms, locked_atoms, updated_at
       FROM balances
       WHERE user_id = $1 AND asset_id = $2`,
      [accountId, assetId]
    );

    if (result.rowCount === 0) return null;

    return this.mapBalanceRow(result.rows[0]);
  }

  /**
   * Update cached balance for an account
   */
  async updateBalance(
    accountId: string,
    assetId: string,
    availableAtoms: bigint,
    lockedAtoms: bigint
  ): Promise<Balance> {
    const result = await this.client.query(
      `INSERT INTO balances (user_id, asset_id, available_atoms, locked_atoms, updated_at)
       VALUES ($1, $2, $3, $4, NOW())
       ON CONFLICT (user_id, asset_id)
       DO UPDATE SET
         available_atoms = EXCLUDED.available_atoms,
         locked_atoms = EXCLUDED.locked_atoms,
         updated_at = NOW()
       RETURNING user_id as account_id, asset_id, available_atoms, locked_atoms, updated_at`,
      [accountId, assetId, availableAtoms.toString(), lockedAtoms.toString()]
    );

    return this.mapBalanceRow(result.rows[0]);
  }

  /**
   * Get all balances for a user (aggregate AVAILABLE and LOCKED accounts)
   */
  async getUserBalances(userId: string): Promise<UserBalance[]> {
    const result = await this.client.query(
      `SELECT 
         la.user_id,
         la.asset_id,
         COALESCE(SUM(
           CASE 
             WHEN la.account_name = 'AVAILABLE' AND le.direction = 'DEBIT' THEN le.amount_atoms
             WHEN la.account_name = 'AVAILABLE' AND le.direction = 'CREDIT' THEN -le.amount_atoms
             ELSE 0
           END
         ), 0) as available_atoms,
         COALESCE(SUM(
           CASE 
             WHEN la.account_name = 'LOCKED' AND le.direction = 'DEBIT' THEN le.amount_atoms
             WHEN la.account_name = 'LOCKED' AND le.direction = 'CREDIT' THEN -le.amount_atoms
             ELSE 0
           END
         ), 0) as locked_atoms
       FROM ledger_accounts la
       LEFT JOIN ledger_entries le ON le.account_id = la.id
       WHERE la.user_id = $1 AND la.account_type = 'USER'
       GROUP BY la.user_id, la.asset_id
       HAVING COALESCE(SUM(
         CASE 
           WHEN le.direction = 'DEBIT' THEN le.amount_atoms
           WHEN le.direction = 'CREDIT' THEN -le.amount_atoms
           ELSE 0
         END
       ), 0) > 0`,
      [userId]
    );

    return result.rows.map((row) => this.mapUserBalanceRow(row));
  }

  /**
   * Recompute balance from ledger entries for a specific account
   * Returns the computed balance (can be compared with cached value)
   */
  async recomputeFromLedger(accountId: string): Promise<{ assetId: string; balance: bigint }[]> {
    const result = await this.client.query(
      `SELECT 
         le.asset_id,
         COALESCE(SUM(
           CASE 
             WHEN le.direction = 'DEBIT' THEN le.amount_atoms
             WHEN le.direction = 'CREDIT' THEN -le.amount_atoms
           END
         ), 0) as balance
       FROM ledger_entries le
       WHERE le.account_id = $1
       GROUP BY le.asset_id`,
      [accountId]
    );

    return result.rows.map((row) => ({
      assetId: row.asset_id,
      balance: BigInt(row.balance)
    }));
  }

  /**
   * Recompute balances for all user accounts of a given asset
   * Useful for reconciliation
   */
  async recomputeAllUserBalances(assetId: string): Promise<void> {
    await this.client.query(
      `WITH computed_balances AS (
         SELECT 
           la.user_id,
           la.asset_id,
           COALESCE(SUM(
             CASE 
               WHEN la.account_name = 'AVAILABLE' AND le.direction = 'DEBIT' THEN le.amount_atoms
               WHEN la.account_name = 'AVAILABLE' AND le.direction = 'CREDIT' THEN -le.amount_atoms
               ELSE 0
             END
           ), 0) as available_atoms,
           COALESCE(SUM(
             CASE 
               WHEN la.account_name = 'LOCKED' AND le.direction = 'DEBIT' THEN le.amount_atoms
               WHEN la.account_name = 'LOCKED' AND le.direction = 'CREDIT' THEN -le.amount_atoms
               ELSE 0
             END
           ), 0) as locked_atoms
         FROM ledger_accounts la
         LEFT JOIN ledger_entries le ON le.account_id = la.id
         WHERE la.asset_id = $1 AND la.account_type = 'USER'
         GROUP BY la.user_id, la.asset_id
       )
       INSERT INTO balances (user_id, asset_id, available_atoms, locked_atoms, updated_at)
       SELECT user_id, asset_id, available_atoms, locked_atoms, NOW()
       FROM computed_balances
       ON CONFLICT (user_id, asset_id)
       DO UPDATE SET
         available_atoms = EXCLUDED.available_atoms,
         locked_atoms = EXCLUDED.locked_atoms,
         updated_at = NOW()`,
      [assetId]
    );
  }

  private mapBalanceRow(row: any): Balance {
    return {
      accountId: row.account_id,
      assetId: row.asset_id,
      availableAtoms: BigInt(row.available_atoms),
      lockedAtoms: BigInt(row.locked_atoms),
      updatedAt: row.updated_at
    };
  }

  private mapUserBalanceRow(row: any): UserBalance {
    return {
      userId: row.user_id,
      assetId: row.asset_id,
      availableAtoms: BigInt(row.available_atoms),
      lockedAtoms: BigInt(row.locked_atoms)
    };
  }
}
