/**
 * Ledger Repository
 * Manages ledger transactions and entries
 */

import type { PoolClient } from "pg";
import type {
  LedgerTransaction,
  LedgerEntry,
  TransactionType,
  EntryDirection
} from "../../domain/types.js";

export class LedgerRepo {
  constructor(private client: PoolClient) {}

  /**
   * Create a ledger transaction header
   */
  async createTransaction(
    txType: TransactionType,
    marketId: string | null,
    seq: bigint | null,
    metadata: Record<string, unknown>
  ): Promise<LedgerTransaction> {
    const result = await this.client.query(
      `INSERT INTO ledger_transactions (tx_type, market_id, seq, metadata)
       VALUES ($1, $2, $3, $4)
       RETURNING id, tx_type, market_id, seq, metadata, created_at`,
      [txType, marketId, seq ? seq.toString() : null, JSON.stringify(metadata)]
    );

    return this.mapTransactionRow(result.rows[0]);
  }

  /**
   * Add a ledger entry to a transaction
   */
  async addEntry(
    transactionId: string,
    accountId: string,
    assetId: string,
    direction: EntryDirection,
    amountAtoms: bigint,
    description: string
  ): Promise<LedgerEntry> {
    const result = await this.client.query(
      `INSERT INTO ledger_entries (transaction_id, account_id, asset_id, direction, amount_atoms, description)
       VALUES ($1, $2, $3, $4, $5, $6)
       RETURNING id, transaction_id, account_id, asset_id, direction, amount_atoms, description, created_at`,
      [transactionId, accountId, assetId, direction, amountAtoms.toString(), description]
    );

    return this.mapEntryRow(result.rows[0]);
  }

  /**
   * Get transaction with all its entries
   */
  async getTransaction(id: string): Promise<{ transaction: LedgerTransaction; entries: LedgerEntry[] } | null> {
    const txResult = await this.client.query(
      `SELECT id, tx_type, market_id, seq, metadata, created_at
       FROM ledger_transactions
       WHERE id = $1`,
      [id]
    );

    if (txResult.rowCount === 0) return null;

    const entriesResult = await this.client.query(
      `SELECT id, transaction_id, account_id, asset_id, direction, amount_atoms, description, created_at
       FROM ledger_entries
       WHERE transaction_id = $1
       ORDER BY created_at`,
      [id]
    );

    return {
      transaction: this.mapTransactionRow(txResult.rows[0]),
      entries: entriesResult.rows.map((row) => this.mapEntryRow(row))
    };
  }

  /**
   * Get entries for a specific account
   */
  async getEntriesByAccount(
    accountId: string,
    limit: number = 100,
    offset: number = 0
  ): Promise<LedgerEntry[]> {
    const result = await this.client.query(
      `SELECT id, transaction_id, account_id, asset_id, direction, amount_atoms, description, created_at
       FROM ledger_entries
       WHERE account_id = $1
       ORDER BY created_at DESC
       LIMIT $2 OFFSET $3`,
      [accountId, limit, offset]
    );

    return result.rows.map((row) => this.mapEntryRow(row));
  }

  /**
   * Get all entries for a transaction
   */
  async getEntriesByTransaction(transactionId: string): Promise<LedgerEntry[]> {
    const result = await this.client.query(
      `SELECT id, transaction_id, account_id, asset_id, direction, amount_atoms, description, created_at
       FROM ledger_entries
       WHERE transaction_id = $1
       ORDER BY created_at`,
      [transactionId]
    );

    return result.rows.map((row) => this.mapEntryRow(row));
  }

  private mapTransactionRow(row: any): LedgerTransaction {
    return {
      id: row.id,
      txType: row.tx_type,
      marketId: row.market_id,
      seq: row.seq ? BigInt(row.seq) : null,
      metadata: row.metadata,
      createdAt: row.created_at
    };
  }

  private mapEntryRow(row: any): LedgerEntry {
    return {
      id: row.id,
      transactionId: row.transaction_id,
      accountId: row.account_id,
      assetId: row.asset_id,
      direction: row.direction,
      amountAtoms: BigInt(row.amount_atoms),
      description: row.description,
      createdAt: row.created_at
    };
  }
}
