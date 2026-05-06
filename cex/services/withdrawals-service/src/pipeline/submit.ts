/**
 * BitGo Submission Pipeline
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import {
  WithdrawalsRepo,
  NetworksRepo,
  AuditRepo,
} from "../db/repositories/index.js";
import type { BitGoClient } from "../bitgo/client.js";
import type { BitGoTransferRequest } from "../bitgo/types.js";

/**
 * Submit withdrawal to BitGo
 */
export async function submitToBitGo(
  client: PoolClient,
  withdrawalId: string,
  bitgoClient: BitGoClient,
  logger: Logger
): Promise<{ success: boolean; message: string }> {
  const withdrawalsRepo = new WithdrawalsRepo(client);
  const networksRepo = new NetworksRepo(client);
  const auditRepo = new AuditRepo(client);

  // 1. Get withdrawal
  const withdrawal = await withdrawalsRepo.findById(withdrawalId);
  if (!withdrawal) {
    return { success: false, message: "Withdrawal not found" };
  }

  // 2. Check status
  if (withdrawal.status !== "APPROVED") {
    return {
      success: false,
      message: `Cannot submit withdrawal in ${withdrawal.status} status`,
    };
  }

  // 3. Get asset network and wallet
  const [assetNetwork, wallet] = await Promise.all([
    networksRepo.getAssetNetwork(withdrawal.assetNetworkId),
    networksRepo.getWallet(withdrawal.assetNetworkId, "HOT"),
  ]);
  if (!assetNetwork) {
    return { success: false, message: "Asset network not found" };
  }
  if (!assetNetwork.enabled) {
    return { success: false, message: "Withdrawals are paused for this asset network" };
  }
  if (assetNetwork.provider !== "BITGO") {
    return {
      success: false,
      message: `Asset network provider ${assetNetwork.provider} cannot be submitted through BitGo`,
    };
  }
  if (!assetNetwork.bitgoCoin) {
    return { success: false, message: "Asset network has no BitGo coin configured" };
  }
  if (!wallet) {
    return { success: false, message: "No wallet configured" };
  }
  if (wallet.provider !== "BITGO") {
    return {
      success: false,
      message: `Wallet provider ${wallet.provider} cannot be submitted through BitGo`,
    };
  }

  // 4. Build BitGo transfer request
  const transferRequest: BitGoTransferRequest = {
    amount: withdrawal.amount.toString(),
    address: withdrawal.destinationAddress,
    memo: withdrawal.destinationTag || undefined,
    sequenceId: withdrawal.idempotencyKey, // Use idempotency key for BitGo
    type: "transfer",
  };
  if (assetNetwork.addressType?.toUpperCase() === "UTXO") {
    transferRequest.txFormat = "psbt";
  }

  try {
    // 5. Submit to BitGo
    logger.info(
      {
        withdrawalId,
        coin: assetNetwork.bitgoCoin,
        walletId: wallet.providerWalletId,
        amount: withdrawal.amount.toString(),
        address: withdrawal.destinationAddress,
      },
      "Submitting withdrawal to BitGo"
    );

    const response = await bitgoClient.createTransfer(
      assetNetwork.bitgoCoin,
      wallet.providerWalletId,
      transferRequest
    );

    // 6. Map BitGo state to our state
    let newStatus: "SIGNING" | "BROADCAST" = "SIGNING";
    if (response.transfer.state === "signed") {
      newStatus = "BROADCAST";
    }

    // 7. Update withdrawal with provider reference
    await withdrawalsRepo.updateStatus(withdrawalId, newStatus, {
      providerRef: response.transfer.id,
      txid: response.transfer.txid || undefined,
    });

    // 8. Log audit event
    await auditRepo.log({
      eventType: "WITHDRAWAL_SUBMITTED",
      withdrawalId,
      userId: withdrawal.userId,
      actorType: "SYSTEM",
      changes: {
        status: newStatus,
        providerRef: response.transfer.id,
        bitgoState: response.transfer.state,
      },
      metadata: {
        coin: assetNetwork.bitgoCoin,
        walletId: wallet.providerWalletId,
      },
    });

    logger.info(
      {
        withdrawalId,
        coin: assetNetwork.bitgoCoin,
        providerRef: response.transfer.id,
        bitgoState: response.transfer.state,
        newStatus,
      },
      "Withdrawal submitted to BitGo successfully"
    );

    return {
      success: true,
      message: `Submitted to BitGo (${response.transfer.state})`,
    };
  } catch (error: any) {
    logger.error(
      {
        error,
        withdrawalId,
        walletId: wallet.providerWalletId,
      },
      "Failed to submit withdrawal to BitGo"
    );

    await auditRepo.log({
      eventType: "WITHDRAWAL_SUBMISSION_FAILED",
      withdrawalId,
      userId: withdrawal.userId,
      actorType: "SYSTEM",
      changes: {
        error: error.message,
      },
    });

    return {
      success: false,
      message: `BitGo submission failed: ${error.message}`,
    };
  }
}
