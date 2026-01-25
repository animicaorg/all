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
      
      // Check if destination is a known deposit address
      if (!knownAddresses.has(tx.to)) continue;
      
      // Parse amount
      const amountAtoms = tx.value;
      
      // Skip if amount is zero or invalid
      if (!amountAtoms || amountAtoms === "0") continue;
      
      deposits.push({
        txid: tx.txid,
        address: tx.to,
        amountAtoms,
        vout: null, // account-based, no vout
      });
      
      this.logger.debug(
        { txid: tx.txid, address: tx.to, amountAtoms },
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
