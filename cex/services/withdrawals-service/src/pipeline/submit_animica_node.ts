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
import type { Withdrawal } from "../db/repositories/withdrawals_repo.js";
import type { Wallet } from "../db/repositories/networks_repo.js";

function getString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function parseBalanceAtoms(value: unknown): bigint {
  const raw =
    typeof value === "object" && value !== null
      ? (value as any).confirmed_balance ??
        (value as any).confirmedBalance ??
        (value as any).spendable_balance ??
        (value as any).spendableBalance ??
        (value as any).balance
      : value;

  if (typeof raw === "number") {
    if (!Number.isSafeInteger(raw) || raw < 0) {
      throw new Error(`Invalid Animica balance value: ${String(raw)}`);
    }
    return BigInt(raw);
  }

  if (typeof raw !== "string") {
    throw new Error(`Invalid Animica balance value: ${String(raw)}`);
  }

  const trimmed = raw.trim();
  if (/^0x[0-9a-fA-F]+$/.test(trimmed)) return BigInt(trimmed);
  if (/^\d+$/.test(trimmed)) return BigInt(trimmed);
  throw new Error(`Invalid Animica balance value: ${raw}`);
}

type FundingSource = {
  kind: "USER_DEPOSIT_ADDRESS" | "HOT_WALLET";
  address: string | null;
  label: string | null;
  walletId: string | null;
};

async function getUserDepositFundingSource(
  client: PoolClient,
  withdrawal: Withdrawal
): Promise<FundingSource | null> {
  const result = await client.query(
    `SELECT
       user_deposit_addresses.address,
       user_deposit_addresses.label,
       wallets.wallet_id
     FROM user_deposit_addresses
     LEFT JOIN wallets ON wallets.id = user_deposit_addresses.wallet_id
     WHERE user_deposit_addresses.user_id = $1
       AND user_deposit_addresses.asset_network_id = $2
       AND user_deposit_addresses.status = 'ACTIVE'
     ORDER BY user_deposit_addresses.assigned_at DESC
     LIMIT 1`,
    [withdrawal.userId, withdrawal.assetNetworkId]
  );

  const row = result.rows[0];
  const address = getString(row?.address);
  if (!address) return null;

  return {
    kind: "USER_DEPOSIT_ADDRESS",
    address,
    label: getString(row?.label),
    walletId: getString(row?.wallet_id),
  };
}

function getHotWalletFundingSource(wallet: Wallet | null): FundingSource | null {
  if (!wallet) return null;

  const address = getString(wallet.metadata?.address);
  const label = getString(wallet.metadata?.wallet_label) || getString(wallet.metadata?.label);

  if (!address && !label) return null;

  return {
    kind: "HOT_WALLET",
    address,
    label,
    walletId: wallet.providerWalletId,
  };
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

  const [assetNetwork, wallet, userDepositSource] = await Promise.all([
    networksRepo.getAssetNetwork(withdrawal.assetNetworkId),
    networksRepo.getWallet(withdrawal.assetNetworkId, "HOT"),
    getUserDepositFundingSource(client, withdrawal),
  ]);
  if (!assetNetwork) return { success: false, message: "Asset network not found" };
  if (wallet && wallet.provider !== "ANIMICA_NODE") {
    return { success: false, message: `Wallet provider ${wallet.provider} cannot be submitted through Animica node` };
  }

  const rpcUrl = getString(wallet?.metadata?.rpc_url) || getString(assetNetwork.metadata?.rpc_url);
  if (!rpcUrl) {
    return { success: false, message: "No RPC URL configured" };
  }

  const fundingSource = userDepositSource ?? getHotWalletFundingSource(wallet);
  if (!fundingSource) {
    return {
      success: false,
      message: "No node-managed Animica funding address configured for this withdrawal",
    };
  }

  try {
    logger.info(
      {
        withdrawalId,
        amountAtoms: withdrawal.amount.toString(),
        feeAtoms: withdrawal.feeAmount.toString(),
        address: withdrawal.destinationAddress,
        fundingSource: fundingSource.kind,
        fromAddress: fundingSource.address,
        fromLabel: fundingSource.label,
      },
      "Submitting withdrawal to Animica node"
    );

    if (fundingSource.address) {
      const balancePayload = await callJsonRpc(rpcUrl, "state.getBalance", [fundingSource.address]);
      const confirmedBalance = parseBalanceAtoms(balancePayload?.result);
      const requiredBalance = withdrawal.totalDebitAmount;
      if (confirmedBalance < requiredBalance) {
        throw new Error(
          `Animica funding address has insufficient confirmed balance: required=${requiredBalance.toString()} available=${confirmedBalance.toString()}`
        );
      }
    }

    const sendRequest: Record<string, string> = {
      to: withdrawal.destinationAddress,
      amount: withdrawal.amount.toString(),
      amountAtoms: withdrawal.amount.toString(),
      fee: withdrawal.feeAmount.toString(),
      feeAtoms: withdrawal.feeAmount.toString(),
    };
    if (fundingSource.address) sendRequest.from = fundingSource.address;
    if (!fundingSource.address && fundingSource.label) sendRequest.label = fundingSource.label;

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
        walletId: fundingSource.walletId,
        fundingSource: fundingSource.kind,
        fromAddress: fundingSource.address,
      },
    });

    return { success: true, message: `Submitted to Animica node (${txid})` };
  } catch (error: any) {
    logger.error({ error, withdrawalId }, "Failed to submit withdrawal to Animica node");
    return { success: false, message: `Animica node submission failed: ${error.message}` };
  }
}
