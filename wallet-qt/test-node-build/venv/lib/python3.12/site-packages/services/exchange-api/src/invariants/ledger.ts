/**
 * Ledger Invariants
 * 
 * Core invariants that must be maintained at all times:
 * 
 * 1. DOUBLE-ENTRY BALANCE: For any ledger transaction, the sum of debits must
 *    equal the sum of credits for each asset.
 * 
 * 2. IMMUTABILITY: Ledger entries can never be updated or deleted once created.
 * 
 * 3. POSITIVE AMOUNTS: All entry amounts must be strictly positive.
 * 
 * 4. ACCOUNT CONSISTENCY: Account balances derived from the ledger must match
 *    the balance cache (within acceptable reconciliation windows).
 * 
 * 5. NO NEGATIVE BALANCES: User available balances cannot go negative
 *    (enforced by requiring sufficient funds before creating debit entries).
 * 
 * 6. FUND LOCKING: Orders must lock sufficient funds before being accepted.
 *    - BUY orders lock quote asset (price * size + fee buffer)
 *    - SELL orders lock base asset (size)
 * 
 * 7. TRADE SETTLEMENT: Trade settlement must:
 *    - Transfer exact traded amounts between parties
 *    - Collect correct fees
 *    - Release locked funds
 *    - Maintain balance across all entries
 * 
 * 8. IDEMPOTENCY: External events (deposits, webhooks, withdrawals) must be
 *    idempotent to prevent double-crediting.
 * 
 * 9. ATOMIC OPERATIONS: All ledger operations must be atomic - either all
 *    entries are created or none are.
 * 
 * 10. AUDIT TRAIL: All ledger transactions must have a clear external reference
 *     for audit purposes.
 */

import { Decimal } from '@prisma/client/runtime/library';
import { EntryDirection, LedgerAccountType } from '@prisma/client';

/**
 * Validates that a set of entries maintains double-entry balance per asset
 */
export function validateDoubleEntry(
  entries: Array<{
    assetId: string;
    direction: EntryDirection;
    amount: Decimal;
  }>
): { valid: boolean; errors: string[] } {
  const errors: string[] = [];
  const assetBalances = new Map<string, { debits: Decimal; credits: Decimal }>();

  for (const entry of entries) {
    // Validate positive amounts
    if (entry.amount.lte(0)) {
      errors.push(`Entry amount must be positive, got ${entry.amount.toString()}`);
      continue;
    }

    if (!assetBalances.has(entry.assetId)) {
      assetBalances.set(entry.assetId, {
        debits: new Decimal(0),
        credits: new Decimal(0),
      });
    }

    const balance = assetBalances.get(entry.assetId)!;
    if (entry.direction === EntryDirection.DEBIT) {
      balance.debits = balance.debits.add(entry.amount);
    } else {
      balance.credits = balance.credits.add(entry.amount);
    }
  }

  // Verify balance for each asset
  for (const [assetId, balance] of assetBalances) {
    if (!balance.debits.equals(balance.credits)) {
      errors.push(
        `Asset ${assetId} is unbalanced: debits=${balance.debits.toString()}, credits=${balance.credits.toString()}`
      );
    }
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Validates sufficient funds for an order lock operation
 */
export function validateOrderLock(
  currentBalance: Decimal,
  requiredAmount: Decimal,
  lockedBalance: Decimal = new Decimal(0)
): { valid: boolean; error?: string } {
  const availableBalance = currentBalance.sub(lockedBalance);
  
  if (availableBalance.lt(requiredAmount)) {
    return {
      valid: false,
      error: `Insufficient funds: available=${availableBalance.toString()}, required=${requiredAmount.toString()}`,
    };
  }

  return { valid: true };
}

/**
 * Calculates required lock amount for a BUY order
 */
export function calculateBuyOrderLock(
  price: Decimal,
  size: Decimal,
  feeRateBps: number
): Decimal {
  const quoteAmount = price.mul(size);
  const feeAmount = quoteAmount.mul(feeRateBps).div(10000);
  return quoteAmount.add(feeAmount);
}

/**
 * Calculates required lock amount for a SELL order
 */
export function calculateSellOrderLock(size: Decimal): Decimal {
  return size;
}

/**
 * Validates trade settlement amounts
 */
export function validateTradeSettlement(
  baseAmount: Decimal,
  quoteAmount: Decimal,
  price: Decimal,
  makerFee: Decimal,
  takerFee: Decimal
): { valid: boolean; errors: string[] } {
  const errors: string[] = [];

  // Verify quote amount matches price * size
  const expectedQuote = price.mul(baseAmount);
  if (!expectedQuote.equals(quoteAmount)) {
    errors.push(
      `Quote amount mismatch: expected=${expectedQuote.toString()}, got=${quoteAmount.toString()}`
    );
  }

  // Verify fees are non-negative
  if (makerFee.lt(0)) {
    errors.push(`Maker fee cannot be negative: ${makerFee.toString()}`);
  }
  if (takerFee.lt(0)) {
    errors.push(`Taker fee cannot be negative: ${takerFee.toString()}`);
  }

  return { valid: errors.length === 0, errors };
}

/**
 * Enforces account type rules
 */
export function validateAccountTypeRules(
  accountType: LedgerAccountType,
  operation: 'debit' | 'credit'
): { valid: boolean; error?: string } {
  // Define rules for each account type
  const rules: Record<LedgerAccountType, { allowDebit: boolean; allowCredit: boolean }> = {
    [LedgerAccountType.AVAILABLE]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.LOCKED]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.FEE]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.CLEARING]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.HOT_WALLET]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.COLD_WALLET]: { allowDebit: true, allowCredit: true },
    [LedgerAccountType.INSURANCE]: { allowDebit: true, allowCredit: true },
  };

  const rule = rules[accountType];
  const allowed = operation === 'debit' ? rule.allowDebit : rule.allowCredit;

  if (!allowed) {
    return {
      valid: false,
      error: `${operation} operation not allowed on ${accountType} account`,
    };
  }

  return { valid: true };
}

/**
 * Constants for invariant enforcement
 */
export const INVARIANTS = {
  // Minimum balance that must be maintained (dust threshold)
  MIN_BALANCE: new Decimal('0.00000001'),
  
  // Maximum precision for amounts (18 decimals)
  MAX_DECIMALS: 18,
  
  // Fee calculation constants
  BASIS_POINTS_DIVISOR: 10000,
  
  // Reconciliation tolerance (for floating point comparison)
  RECONCILIATION_TOLERANCE: new Decimal('0.000000000000000001'),
} as const;

/**
 * Checks if two decimal values are equal within tolerance
 */
export function isEqual(a: Decimal, b: Decimal, tolerance = INVARIANTS.RECONCILIATION_TOLERANCE): boolean {
  return a.sub(b).abs().lte(tolerance);
}

/**
 * Validates that a balance is non-negative
 */
export function validateNonNegativeBalance(balance: Decimal): { valid: boolean; error?: string } {
  if (balance.lt(0)) {
    return {
      valid: false,
      error: `Balance cannot be negative: ${balance.toString()}`,
    };
  }
  return { valid: true };
}

/**
 * Type guard for checking if an error is an invariant violation
 */
export class InvariantViolationError extends Error {
  constructor(message: string, public readonly invariant: string) {
    super(message);
    this.name = 'InvariantViolationError';
  }
}
