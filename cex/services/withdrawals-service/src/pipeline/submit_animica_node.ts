/**
 * Animica node submission pipeline.
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import {
  WithdrawalsRepo,
  NetworksRepo,
  AuditRepo,
} from "../db/repositories/index.js";

function getString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

async function callJsonRpc(rpcUrl: string, method: string, params: unknown[]) {
  const headers: Record<string, string> = { "content-type": "application/json" };
  const adminToken = process.env.ANIMICA_RPC_ADMIN_TOKEN?.trim();
  if (adminToken) headers["x-animica-admin-token"] = adminToken;

  const response = await fetch(rpcUrl, {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "2.0",
      id: "cex-withdrawal",
      method,
      params,
    }),
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok || payload?.error) {
    throw new Error(payload?.error?.message || `${method} RPC call failed`);
  }

  return payload;
}

export async function submitToAnimicaNode(
  client: PoolClient,
  withdrawalId: string,
  logger: Logger
): Promise<{ success: boolean; message: string }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const networksRepo = new NetworksRepo(client);
  const auditRepo = new AuditRepo(client);

  const withdrawal = await withdrawalsRepo.findById(withdrawalId);
  if (!withdrawal) return { success: false, message: "Withdrawal not found" };
  if (withdrawal.status !== "APPROVED") {
    return { success: false, message: `Cannot submit withdrawal in ${withdrawal.status} status` };
  }

  const [assetNetwork, wallet] = await Promise.all([
    networksRepo.getAssetNetwork(withdrawal.assetNetworkId),
    networksRepo.getWallet(withdrawal.assetNetworkId, "HOT"),
  ]);
  if (!assetNetwork) return { success: false, message: "Asset network not found" };
  if (!wallet) {
    await withdrawalsRepo.updateStatus(withdrawalId, "FAILED", {
      failureCode: "NO_WALLET",
      failureMessage: "No hot wallet configured for this asset network",
    });
    return { success: false, message: "No wallet configured" };
  }
  if (wallet.provider !== "ANIMICA_NODE") {
    return { success: false, message: `Wallet provider ${wallet.provider} cannot be submitted through Animica node` };
  }

  const rpcUrl = getString(wallet.metadata?.rpc_url) || getString(assetNetwork.metadata?.rpc_url);
  if (!rpcUrl) {
    await withdrawalsRepo.updateStatus(withdrawalId, "FAILED", {
      failureCode: "NO_RPC_URL",
      failureMessage: "No Animica RPC URL configured",
    });
    return { success: false, message: "No RPC URL configured" };
  }

  try {
    logger.info(
      {
        withdrawalId,
        amountAtoms: withdrawal.amount.toString(),
        feeAtoms: withdrawal.feeAmount.toString(),
        address: withdrawal.destinationAddress,
      },
      "Submitting withdrawal to Animica node"
    );

    const sendRequest: Record<string, string> = {
      to: withdrawal.destinationAddress,
      amount: withdrawal.amount.toString(),
      amountAtoms: withdrawal.amount.toString(),
      fee: withdrawal.feeAmount.toString(),
      feeAtoms: withdrawal.feeAmount.toString(),
    };
    const walletLabel =
      getString(wallet.metadata?.wallet_label) ||
      getString(wallet.metadata?.label) ||
      getString(wallet.providerWalletId);
    const fromAddress = getString(wallet.metadata?.address);
    if (walletLabel) sendRequest.label = walletLabel;
    if (fromAddress) sendRequest.from = fromAddress;

    const payload = await callJsonRpc(rpcUrl, "wallet.send", [sendRequest]);
    const txid =
      getString(payload?.result?.txid) ||
      getString(payload?.result?.txHash) ||
      getString(payload?.result?.tx_hash) ||
      getString(payload?.result?.hash) ||
      getString(payload?.result);
    if (!txid) throw new Error("Animica node did not return a txid");

    await withdrawalsRepo.updateStatus(withdrawalId, "BROADCAST", {
      providerRef: txid,
      txid,
    });

    await auditRepo.log({
      eventType: "WITHDRAWAL_SUBMITTED",
      withdrawalId,
      userId: withdrawal.userId,
      actorType: "SYSTEM",
      changes: {
        status: "BROADCAST",
        providerRef: txid,
      },
      metadata: {
        provider: "ANIMICA_NODE",
        walletId: wallet.providerWalletId,
      },
    });

    return { success: true, message: `Submitted to Animica node (${txid})` };
  } catch (error: any) {
    logger.error({ error, withdrawalId }, "Failed to submit withdrawal to Animica node");
    await withdrawalsRepo.updateStatus(withdrawalId, "FAILED", {
      failureCode: "ANIMICA_NODE_ERROR",
      failureMessage: error.message || "Failed to submit to Animica node",
      incrementAttempt: true,
      nextRetryAt: new Date(Date.now() + 60000),
    });
    return { success: false, message: `Animica node submission failed: ${error.message}` };
  }
}
