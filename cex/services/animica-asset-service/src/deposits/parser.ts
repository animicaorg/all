/**
 * Transaction Parser
 * 
 * Parses Animica transactions to extract deposit information
 * Supports account-based model (to/from/value structure)
 */

import type { TransactionInfo } from "../rpc/types.js";
import type { Logger } from "pino";

export interface ParsedDeposit {
  txid: string;
  address: string;
  amountAtoms: string;
  vout: string | null; // null for account-based, index for UTXO
}

export class TransactionParser {
  constructor(private logger: Logger) {}

  private normalizeAddress(address: string): string {
    return address.trim().toLowerCase();
  }
  
  /**
   * Parse transactions for deposits to known addresses
   * 
   * Animica is account-based, so we check:
   * - tx.to matches a deposit address
   * - tx.value > 0
   */
  parseDeposits(
    txs: TransactionInfo[],
    knownAddresses: Set<string>
  ): ParsedDeposit[] {
    const deposits: ParsedDeposit[] = [];
    
    for (const tx of txs) {
      // Skip if no destination
      if (!tx.to) continue;

      const normalizedTo = this.normalizeAddress(tx.to);
      
      // Check if destination is a known deposit address
      if (!knownAddresses.has(normalizedTo)) continue;
      
      // Parse amount
      const amountAtoms = String(tx.value);
      
      // Skip if amount is zero or invalid
      if (!/^\d+$/.test(amountAtoms) || BigInt(amountAtoms) === 0n) continue;
      
      deposits.push({
        txid: tx.txid,
        address: normalizedTo,
        amountAtoms,
        vout: null, // account-based, no vout
      });
      
      this.logger.debug(
        { txid: tx.txid, address: normalizedTo, amountAtoms },
        "Deposit detected in transaction"
      );
    }
    
    return deposits;
  }
  
  /**
   * Create deduplication key for a deposit
   * Format: <txid>:<vout> or <txid>:0 for account-based
   */
  createDepositKey(txid: string, vout: string | null): string {
    return `${txid}:${vout || "0"}`;
  }
}
