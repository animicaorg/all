/**
 * Risk Evaluation Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import type { WithdrawalPolicy } from "../db/repositories/index.js";

export interface RiskDecision {
  decision: "ALLOW" | "REVIEW" | "BLOCK";
  score: number;
  flags: string[];
  reason: string | null;
  requiredApprovals: number;
}

/**
 * Evaluate risk for a withdrawal request
 */
export async function evaluateRisk(
  client: PoolClient,
  userId: string,
  assetNetworkId: string,
  amount: bigint,
  destinationAddress: string,
  policy: WithdrawalPolicy,
  logger: Logger
): Promise<RiskDecision> {
  const flags: string[] = [];
  let score = 0;
  let reason: string | null = null;
  let requiredApprovals = policy.requiredApprovals;

  // 1. Check if amount exceeds high risk threshold
  if (policy.highRiskThresholdAtoms && amount >= policy.highRiskThresholdAtoms) {
    flags.push("HIGH_AMOUNT");
    score += 50;
    requiredApprovals = Math.max(requiredApprovals, policy.highRiskApprovals);
  }

  // 2. Check velocity limits (24-hour window)
  const velocityCheck = await checkVelocityLimits(
    client,
    userId,
    assetNetworkId,
    amount,
    policy
  );

  if (velocityCheck.exceeded) {
    flags.push("VELOCITY_EXCEEDED");
    score += 30;
    reason = velocityCheck.reason;
  }

  // 3. Check if address is in whitelist (if required)
  if (policy.whitelistOnly) {
    const isWhitelisted = await checkAddressWhitelist(
      client,
      userId,
      destinationAddress
    );
    
    if (!isWhitelisted) {
      flags.push("ADDRESS_NOT_WHITELISTED");
      score = 100;
      reason = "Destination address not in whitelist";
      return {
        decision: "BLOCK",
        score,
        flags,
        reason,
        requiredApprovals,
      };
    }
  }

  // 4. Check if this is a new address
  const isNewAddress = await checkIsNewAddress(
    client,
    userId,
    destinationAddress
  );

  if (isNewAddress) {
    flags.push("NEW_ADDRESS");
    score += 20;
  }

  // 5. Determine decision based on score
  let decision: RiskDecision["decision"];
  
  if (score >= 80) {
    decision = "BLOCK";
    reason = reason || "High risk score";
  } else if (score >= 40 || flags.some((flag) => flag !== "NEW_ADDRESS")) {
    decision = "REVIEW";
    reason = reason || "Requires manual review";
  } else {
    decision = "ALLOW";
  }

  logger.debug(
    {
      userId,
      assetNetworkId,
      amount: amount.toString(),
      score,
      flags,
      decision,
    },
    "Risk evaluation completed"
  );

  return {
    decision,
    score,
    flags,
    reason,
    requiredApprovals,
  };
}

/**
 * Check if user has exceeded velocity limits
 */
async function checkVelocityLimits(
  client: PoolClient,
  userId: string,
  assetNetworkId: string,
  amount: bigint,
  policy: WithdrawalPolicy
): Promise<{ exceeded: boolean; reason: string | null }> {
  // Check amount limit
  if (policy.dailyLimitAtoms) {
    const result = await client.query(
      `SELECT COALESCE(SUM(total_debit_amount::numeric), 0) as total
       FROM withdrawals
       WHERE user_id = $1
         AND asset_network_id = $2
         AND status NOT IN ('REJECTED', 'CANCELED', 'FAILED')
         AND created_at >= NOW() - INTERVAL '24 hours'`,
      [userId, assetNetworkId]
    );

    const dailyTotal = BigInt(result.rows[0].total);
    const newTotal = dailyTotal + amount;

    if (newTotal > policy.dailyLimitAtoms) {
      return {
        exceeded: true,
        reason: `Daily withdrawal limit exceeded (${newTotal.toString()}/${policy.dailyLimitAtoms.toString()})`,
      };
    }
  }

  // Check count limit
  if (policy.dailyLimitCount) {
    const result = await client.query(
      `SELECT COUNT(*) as count
       FROM withdrawals
       WHERE user_id = $1
         AND asset_network_id = $2
         AND status NOT IN ('REJECTED', 'CANCELED', 'FAILED')
         AND created_at >= NOW() - INTERVAL '24 hours'`,
      [userId, assetNetworkId]
    );

    const dailyCount = parseInt(result.rows[0].count, 10);

    if (dailyCount >= policy.dailyLimitCount) {
      return {
        exceeded: true,
        reason: `Daily withdrawal count limit exceeded (${dailyCount}/${policy.dailyLimitCount})`,
      };
    }
  }

  return { exceeded: false, reason: null };
}

/**
 * Check if address is in user's whitelist
 */
async function checkAddressWhitelist(
  client: PoolClient,
  userId: string,
  address: string
): Promise<boolean> {
  // TODO: Implement whitelist table and check
  // For now, return true (no whitelist enforced)
  return true;
}

/**
 * Check if this is a new address for the user
 */
async function checkIsNewAddress(
  client: PoolClient,
  userId: string,
  address: string
): Promise<boolean> {
  const result = await client.query(
    `SELECT 1 FROM withdrawals
     WHERE user_id = $1
       AND destination_address = $2
       AND status = 'CONFIRMED'
     LIMIT 1`,
    [userId, address]
  );

  return result.rowCount === 0;
}
