/**
 * Risk Checks for Deposits
 * 
 * Baseline risk checks to flag suspicious deposits
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import type { Deposit } from "../db/repositories/deposits_repo.js";
import type { RiskCheckResult } from "../bitgo/types.js";

/**
 * Run risk checks on a deposit
 */
export async function runRiskChecks(
  deposit: Deposit,
  client: PoolClient,
  logger: Logger
): Promise<RiskCheckResult> {
  const flags: string[] = [];
  let hold = false;
  let reason: string | undefined;

  // Check 1: Amount sanity
  if (deposit.amountAtoms <= 0n) {
    flags.push("ZERO_OR_NEGATIVE_AMOUNT");
    hold = true;
    reason = "Amount is zero or negative";
  }

  // Check 2: Absurdly large amount (> 1 million USD equivalent, simplified)
  // For a real system, you'd convert to USD based on current prices
  const MAX_AMOUNT_ATOMS = 100000000000n; // 100 billion atoms (arbitrary threshold)
  if (deposit.amountAtoms > MAX_AMOUNT_ATOMS) {
    flags.push("ABNORMALLY_LARGE_AMOUNT");
    hold = true;
    reason = "Amount exceeds maximum threshold";
  }

  // Check 3: Unassigned address
  if (deposit.unassigned) {
    flags.push("UNASSIGNED_ADDRESS");
    // Don't hold, but flag for review
  }

  // Check 4: Token contract allowlist (for ERC20)
  // This check should happen during normalization, but we can double-check
  // If raw payload contains token contract, verify it matches asset_networks
  if (deposit.raw?.tokenContractAddress) {
    const contractMatch = await verifyTokenContract(
      client,
      deposit.assetNetworkId,
      deposit.raw.tokenContractAddress
    );
    
    if (!contractMatch) {
      flags.push("UNKNOWN_TOKEN_CONTRACT");
      hold = true;
      reason = "Token contract not in allowlist";
    }
  }

  // Check 5: Velocity check - too many deposits in short period
  if (deposit.userId) {
    const recentDeposits = await getRecentDepositCount(
      client,
      deposit.userId,
      5 // 5 minutes
    );
    
    if (recentDeposits > 10) {
      flags.push("HIGH_VELOCITY");
      hold = true;
      reason = "Too many deposits in short period";
    }
  }

  // Check 6: Duplicate txid with different address (suspicious)
  const duplicateTxid = await checkDuplicateTxid(
    client,
    deposit.txid,
    deposit.address,
    deposit.id
  );
  
  if (duplicateTxid) {
    flags.push("DUPLICATE_TXID_DIFFERENT_ADDRESS");
    // Flag but don't necessarily hold (could be valid multi-output tx)
  }

  const ok = flags.length === 0;

  if (flags.length > 0) {
    logger.info(
      { depositId: deposit.id, flags, hold, reason },
      "Risk checks flagged deposit"
    );
  }

  return {
    ok,
    hold,
    reason,
    flags,
  };
}

/**
 * Verify token contract matches asset_networks
 */
async function verifyTokenContract(
  client: PoolClient,
  assetNetworkId: string,
  contractAddress: string
): Promise<boolean> {
  const result = await client.query(
    `SELECT id FROM asset_networks
     WHERE id = $1
       AND LOWER(contract_address) = LOWER($2)`,
    [assetNetworkId, contractAddress]
  );
  
  return result.rows.length > 0;
}

/**
 * Get recent deposit count for user
 */
async function getRecentDepositCount(
  client: PoolClient,
  userId: string,
  minutes: number
): Promise<number> {
  const result = await client.query(
    `SELECT COUNT(*) as count
     FROM deposits
     WHERE user_id = $1
       AND created_at > NOW() - INTERVAL '1 minute' * $2`,
    [userId, minutes]
  );
  
  return parseInt(result.rows[0].count);
}

/**
 * Check for duplicate txid with different address
 */
async function checkDuplicateTxid(
  client: PoolClient,
  txid: string,
  address: string,
  currentDepositId: string
): Promise<boolean> {
  const result = await client.query(
    `SELECT id
     FROM deposits
     WHERE txid = $1
       AND address != $2
       AND id != $3
     LIMIT 1`,
    [txid, address, currentDepositId]
  );
  
  return result.rows.length > 0;
}
