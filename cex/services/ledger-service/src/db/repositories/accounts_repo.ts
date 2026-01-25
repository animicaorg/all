/**
 * Ledger Accounts Repository
 * Manages the chart of accounts (USER:AVAILABLE, USER:LOCKED, SYSTEM:FEE, etc.)
 */

import type { PoolClient } from "pg";
import type { LedgerAccount, AccountType, AccountName } from "../../domain/types.js";

export class AccountsRepo {
  constructor(private client: PoolClient) {}

  /**
   * Get account by ID
   */
  async getById(accountId: string): Promise<LedgerAccount | null> {
    const result = await this.client.query(
      `SELECT id, account_type, account_name, user_id, asset_id, created_at
       FROM ledger_accounts
       WHERE id = $1`,
      [accountId]
    );

    if (result.rowCount === 0) return null;

    return this.mapRow(result.rows[0]);
  }

  /**
   * Get or create user accounts for a specific asset
   * Returns { available, locked } account IDs
   */
  async ensureUserAccounts(
    userId: string,
    assetId: string
  ): Promise<{ available: LedgerAccount; locked: LedgerAccount }> {
    const available = await this.ensureAccount("USER", "AVAILABLE", userId, assetId);
    const locked = await this.ensureAccount("USER", "LOCKED", userId, assetId);

    return { available, locked };
  }

  /**
   * Get or create a system account for a specific asset
   */
  async ensureSystemAccount(
    accountName: AccountName,
    assetId: string
  ): Promise<LedgerAccount> {
    return this.ensureAccount("SYSTEM", accountName, null, assetId);
  }

  /**
   * Ensure an account exists, creating it if necessary
   * Uses ON CONFLICT to handle concurrent creation attempts
   */
  private async ensureAccount(
    accountType: AccountType,
    accountName: AccountName,
    userId: string | null,
    assetId: string
  ): Promise<LedgerAccount> {
    const result = await this.client.query(
      `INSERT INTO ledger_accounts (account_type, account_name, user_id, asset_id)
       VALUES ($1, $2, $3, $4)
       ON CONFLICT (user_id, asset_id, account_name) DO NOTHING
       RETURNING id, account_type, account_name, user_id, asset_id, created_at`,
      [accountType, accountName, userId, assetId]
    );

    // If insert returned a row, we created it. Otherwise, fetch existing.
    if (result.rowCount && result.rowCount > 0) {
      return this.mapRow(result.rows[0]);
    }

    // Account already existed, fetch it
    const existing = await this.client.query(
      `SELECT id, account_type, account_name, user_id, asset_id, created_at
       FROM ledger_accounts
       WHERE account_type = $1 AND account_name = $2
         AND (user_id = $3 OR ($3 IS NULL AND user_id IS NULL))
         AND asset_id = $4`,
      [accountType, accountName, userId, assetId]
    );

    if (existing.rowCount === 0) {
      throw new Error(
        `Account not found after insert: ${accountType}:${accountName} user=${userId} asset=${assetId}`
      );
    }

    return this.mapRow(existing.rows[0]);
  }

  /**
   * Get all accounts for a user
   */
  async getUserAccounts(userId: string): Promise<LedgerAccount[]> {
    const result = await this.client.query(
      `SELECT id, account_type, account_name, user_id, asset_id, created_at
       FROM ledger_accounts
       WHERE user_id = $1
       ORDER BY asset_id, account_name`,
      [userId]
    );

    return result.rows.map((row) => this.mapRow(row));
  }

  /**
   * Get all system accounts
   */
  async getSystemAccounts(): Promise<LedgerAccount[]> {
    const result = await this.client.query(
      `SELECT id, account_type, account_name, user_id, asset_id, created_at
       FROM ledger_accounts
       WHERE account_type = 'SYSTEM'
       ORDER BY asset_id, account_name`
    );

    return result.rows.map((row) => this.mapRow(row));
  }

  /**
   * Find account by criteria
   */
  async findAccount(
    accountType: AccountType,
    accountName: AccountName,
    userId: string | null,
    assetId: string
  ): Promise<LedgerAccount | null> {
    const result = await this.client.query(
      `SELECT id, account_type, account_name, user_id, asset_id, created_at
       FROM ledger_accounts
       WHERE account_type = $1 AND account_name = $2
         AND (user_id = $3 OR ($3 IS NULL AND user_id IS NULL))
         AND asset_id = $4`,
      [accountType, accountName, userId, assetId]
    );

    if (result.rowCount === 0) return null;

    return this.mapRow(result.rows[0]);
  }

  private mapRow(row: any): LedgerAccount {
    return {
      id: row.id,
      accountType: row.account_type,
      accountName: row.account_name,
      userId: row.user_id,
      assetId: row.asset_id,
      createdAt: row.created_at
    };
  }
}
