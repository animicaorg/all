/**
 * BitGo Webhook Normalization
 */

import type { BitGoWebhookPayload, WithdrawalObservation } from "./types.js";

/**
 * Normalize BitGo webhook payload to WithdrawalObservation
 */
export function normalizeWebhookToObservation(
  webhook: BitGoWebhookPayload
): WithdrawalObservation | null {
  if (!webhook.transfer) {
    return null;
  }

  const transfer = webhook.transfer;
  
  // Map BitGo state to our state
  let state: WithdrawalObservation["state"];
  switch (transfer.state) {
    case "pending":
    case "pendingApproval":
      state = "SIGNING";
      break;
    case "signed":
      state = "BROADCAST";
      break;
    case "confirmed":
      state = "CONFIRMED";
      break;
    case "failed":
    case "rejected":
    case "removed":
      state = "FAILED";
      break;
    default:
      state = "SIGNING";
  }

  return {
    provider: "BITGO",
    providerRef: transfer.id,
    walletId: transfer.wallet,
    txid: transfer.txid || null,
    state,
    amountAtoms: BigInt(transfer.value),
    observedAt: new Date(),
    raw: webhook,
  };
}
