/**
 * Bitcoin-style node submission pipeline.
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

function formatAtoms(amount: bigint, decimals: number): string {
  if (decimals <= 0) return amount.toString();
  const scale = 10n ** BigInt(decimals);
  const whole = amount / scale;
  const fraction = amount % scale;
  if (fraction === 0n) return whole.toString();
  return `${whole.toString()}.${fraction.toString().padStart(decimals, "0").replace(/0+$/, "")}`;
}

async function callJsonRpc(rpcUrl: string, method: string, params: unknown[]) {
  const url = new URL(rpcUrl);
  const headers: Record<string, string> = { "content-type": "application/json" };

  if (url.username || url.password) {
    const username = decodeURIComponent(url.username);
    const password = decodeURIComponent(url.password);
    headers.authorization = `Basic ${Buffer.from(`${username}:${password}`).toString("base64")}`;
    url.username = "";
    url.password = "";
  }

  const response = await fetch(url.toString(), {
    method: "POST",
    headers,
    body: JSON.stringify({
      jsonrpc: "1.0",
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

export async function submitToBitcoinNode(
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
  if (wallet.provider !== "BITCOIN_NODE") {
    return { success: false, message: `Wallet provider ${wallet.provider} cannot be submitted through a Bitcoin-style node` };
  }

  const rpcUrl = getString(wallet.metadata?.rpc_url) || getString(assetNetwork.metadata?.rpc_url);
  if (!rpcUrl) {
    await withdrawalsRepo.updateStatus(withdrawalId, "FAILED", {
      failureCode: "NO_RPC_URL",
      failureMessage: "No Bitcoin-style RPC URL configured",
    });
    return { success: false, message: "No RPC URL configured" };
  }

  const amount = formatAtoms(withdrawal.amount, assetNetwork.assetDecimals);

  try {
    logger.info(
      {
        withdrawalId,
        asset: assetNetwork.assetSymbol,
        amount,
        address: withdrawal.destinationAddress,
      },
      "Submitting withdrawal to Bitcoin-style node"
    );

    const payload = await callJsonRpc(rpcUrl, "sendtoaddress", [
      withdrawal.destinationAddress,
      Number(amount),
      `cex withdrawal ${withdrawal.id}`,
    ]);
    const txid = getString(payload?.result);
    if (!txid) throw new Error("Bitcoin-style node did not return a txid");

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
        provider: "BITCOIN_NODE",
        walletId: wallet.providerWalletId,
      },
    });

    return { success: true, message: `Submitted to Bitcoin-style node (${txid})` };
  } catch (error: any) {
    logger.error({ error, withdrawalId }, "Failed to submit withdrawal to Bitcoin-style node");
    await withdrawalsRepo.updateStatus(withdrawalId, "FAILED", {
      failureCode: "BITCOIN_NODE_ERROR",
      failureMessage: error.message || "Failed to submit to Bitcoin-style node",
      incrementAttempt: true,
      nextRetryAt: new Date(Date.now() + 60000),
    });
    return { success: false, message: `Bitcoin-style node submission failed: ${error.message}` };
  }
}
