/**
 * Withdrawal Request Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import {
  WithdrawalsRepo,
  PolicyRepo,
  NetworksRepo,
  AuditRepo,
  type CreateWithdrawalParams,
} from "../db/repositories/index.js";
import { evaluateRisk, type RiskDecision } from "./risk.js";
import { enqueueOperation } from "../outbox/outbox.js";

export interface WithdrawalRequest {
  assetNetworkId: string;
  destinationAddress: string;
  destinationTag?: string;
  amount: bigint;
  clientWithdrawalId?: string;
}

/**
 * Validate and create a withdrawal request
 */
export async function validateAndCreateWithdrawal(
  client: PoolClient,
  userId: string,
  request: WithdrawalRequest,
  idempotencyKey: string,
  logger: Logger
): Promise<{ withdrawalId: string; status: string; riskDecision: RiskDecision }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const policyRepo = new PolicyRepo(client);
  const networksRepo = new NetworksRepo(client);
  const auditRepo = new AuditRepo(client);

  // 1. Validate asset network exists and is enabled
  const assetNetwork = await networksRepo.getAssetNetwork(request.assetNetworkId);
  if (!assetNetwork) {
    throw new Error("Asset network not found");
  }
  if (!assetNetwork.enabled) {
    throw new Error("Asset network is disabled");
  }

  // 2. Get withdrawal policy
  const policy = await policyRepo.getByAssetNetwork(request.assetNetworkId);
  if (!policy) {
    throw new Error("No withdrawal policy configured for this asset");
  }
  if (!policy.enabled) {
    throw new Error("Withdrawals disabled for this asset");
  }

  // 3. Validate amount against policy
  if (request.amount < policy.minWithdrawalAtoms) {
    throw new Error(
      `Amount below minimum withdrawal (${policy.minWithdrawalAtoms.toString()})`
    );
  }
  if (policy.maxWithdrawalAtoms && request.amount > policy.maxWithdrawalAtoms) {
    throw new Error(
      `Amount exceeds maximum withdrawal (${policy.maxWithdrawalAtoms.toString()})`
    );
  }

  // 4. Calculate fee (for now, use a simple fee from policy metadata or default)
  const feeAmount = BigInt(policy.metadata?.withdrawalFeeAtoms || "0");

  // 5. Evaluate risk
  const riskDecision = await evaluateRisk(
    client,
    userId,
    request.assetNetworkId,
    request.amount,
    request.destinationAddress,
    policy,
    logger
  );

  // 6. Create withdrawal record
  const createParams: CreateWithdrawalParams = {
    userId,
    assetNetworkId: request.assetNetworkId,
    destinationAddress: request.destinationAddress,
    destinationTag: request.destinationTag,
    amount: request.amount,
    feeAmount,
    provider: assetNetwork.provider,
    idempotencyKey,
    clientWithdrawalId: request.clientWithdrawalId,
    riskScore: riskDecision.score,
    riskFlags: riskDecision.flags,
    riskReason: riskDecision.reason ?? undefined,
  };

  const withdrawal = await withdrawalsRepo.create(createParams);

  // 7. Determine initial status based on risk
  let status = "REQUESTED";
  if (riskDecision.decision === "BLOCK") {
    status = "REJECTED";
    await withdrawalsRepo.updateStatus(withdrawal.id, "REJECTED", {
      failureCode: "RISK_BLOCK",
      failureMessage: riskDecision.reason || "Blocked by risk evaluation",
    });
  } else if (riskDecision.decision === "REVIEW") {
    status = "RISK_REVIEW";
    await withdrawalsRepo.updateStatus(withdrawal.id, "RISK_REVIEW");
  } else if (riskDecision.decision === "ALLOW") {
    // Low-risk withdrawals are automatically approved and submitted by the outbox worker.
    status = "APPROVED";
    await withdrawalsRepo.updateStatus(withdrawal.id, "APPROVED");
  }

  // 8. Log audit event
  await auditRepo.log({
    eventType: "WITHDRAWAL_REQUESTED",
    withdrawalId: withdrawal.id,
    userId,
    actorType: "USER",
    changes: {
      status: "REQUESTED",
      amount: request.amount.toString(),
      destination: request.destinationAddress,
    },
    metadata: {
      riskDecision,
      idempotencyKey,
    },
  });

  // 9. Queue ledger lock operation if approved or pending review
  if (status !== "REJECTED") {
    await enqueueOperation(client, withdrawal.id, "APPLY_LEDGER_LOCK", {
      userId,
      assetNetworkId: request.assetNetworkId,
      amount: withdrawal.totalDebitAmount.toString(),
      withdrawalId: withdrawal.id,
    });
  }

  logger.info(
    {
      withdrawalId: withdrawal.id,
      userId,
      status,
      riskDecision: riskDecision.decision,
    },
    "Withdrawal request created"
  );

  return {
    withdrawalId: withdrawal.id,
    status,
    riskDecision,
  };
}
