/**
 * Core domain types for the ledger service
 */

/**
 * Account types in the chart of accounts
 */
export type AccountType = "USER" | "SYSTEM";

/**
 * Account names for different purposes
 */
export type AccountName = 
  | "AVAILABLE"  // User's available balance
  | "LOCKED"     // User's locked balance (in orders)
  | "FEE"        // System fee collection
  | "CLEARING"   // System clearing account
  | "INSURANCE"; // System insurance fund

/**
 * Ledger account
 */
export interface LedgerAccount {
  id: string;
  accountType: AccountType;
  accountName: AccountName;
  userId: string | null; // null for system accounts
  assetId: string;
  createdAt: Date;
}

/**
 * Transaction types
 */
export type TransactionType =
  | "TRADE_SETTLE"  // Settlement of a trade
  | "TRANSFER"      // Move between user accounts (lock/unlock)
  | "DEPOSIT"       // Deposit from blockchain
  | "WITHDRAWAL"    // Withdrawal to blockchain
  | "FEE"           // Fee collection
  | "ADJUSTMENT";   // Manual adjustment (admin)

/**
 * Ledger transaction header
 */
export interface LedgerTransaction {
  id: string;
  txType: TransactionType;
  marketId: string | null;
  seq: bigint | null;
  metadata: Record<string, unknown>;
  createdAt: Date;
}

/**
 * Entry direction (debit or credit)
 */
export type EntryDirection = "DEBIT" | "CREDIT";

/**
 * Ledger entry (individual debit or credit)
 */
export interface LedgerEntry {
  id: string;
  transactionId: string;
  accountId: string;
  assetId: string;
  direction: EntryDirection;
  amountAtoms: bigint;
  description: string;
  createdAt: Date;
}

/**
 * Balance (derived from ledger entries or cached)
 */
export interface Balance {
  accountId: string;
  assetId: string;
  availableAtoms: bigint;
  lockedAtoms: bigint;
  updatedAt: Date;
}

/**
 * User balance view (aggregated across AVAILABLE and LOCKED accounts)
 */
export interface UserBalance {
  userId: string;
  assetId: string;
  availableAtoms: bigint;
  lockedAtoms: bigint;
}

/**
 * Order lock tracking
 */
export interface OrderLock {
  orderId: string;
  userId: string;
  assetId: string;
  lockedAtoms: bigint;
  usedAtoms: bigint;
  createdAt: Date;
  updatedAt: Date;
}

/**
 * Event offset tracking
 */
export interface LedgerEventOffset {
  marketId: string;
  consumerGroup: string;
  lastTradeSeq: bigint;
  lastOrderSeq: bigint;
  updatedAt: Date;
}

/**
 * Reconciliation report
 */
export interface ReconciliationReport {
  id: string;
  jobType: "BALANCE_RECOMPUTE" | "INVARIANT_CHECK" | "GAP_DETECT";
  ok: boolean;
  mismatches: Array<{
    accountId: string;
    assetId: string;
    expected: string;
    actual: string;
  }>;
  summary: Record<string, unknown>;
  runAt: Date;
}

/**
 * Trade event from matching engine (consumed from NATS)
 */
export interface TradeEvent {
  tradeId: string;
  marketId: string;
  makerOrderId: string;
  takerOrderId: string;
  priceAtoms: string;
  sizeAtoms: string;
  quoteAmountAtoms: string;
  makerFeeAtoms: string;
  takerFeeAtoms: string;
  feeAsset: string;
  feeBpsMaker: number;
  feeBpsTaker: number;
  sequence: string;
  createdAt: string;
}

/**
 * Order event from matching engine
 */
export interface OrderEvent {
  eventType: "ACCEPTED" | "PARTIAL_FILL" | "FILLED" | "CANCELED" | "REJECTED" | "EXPIRED";
  orderId: string;
  userId: string;
  clientOrderId: string;
  marketId: string;
  side: "BUY" | "SELL";
  orderType: "LIMIT" | "MARKET";
  priceAtoms: string;
  sizeAtoms: string;
  filledAtoms: string;
  remainingAtoms: string;
  status: string;
  sequence: string;
}

/**
 * Market configuration (minimal info needed by ledger)
 */
export interface Market {
  id: string;
  symbol: string;
  baseAsset: string;
  quoteAsset: string;
  makerFeeBps: number;
  takerFeeBps: number;
  feeAsset: string;
}
