/**
 * BitGo Types
 */

export interface BitGoTransferRequest {
  amount: string; // Amount in base units (satoshis, wei, etc.)
  address: string;
  memo?: string; // For MEMO_BASED networks
  sequenceId?: string; // Idempotency key
  type?: "transfer";
  txFormat?: "psbt";
  walletPassphrase?: string;
}

export interface BitGoTransferResponse {
  transfer: {
    id: string;
    coin: string;
    wallet: string;
    txid?: string;
    state: "pending" | "pendingApproval" | "signed" | "rejected" | "removed" | "confirmed" | "failed";
    value: string;
    valueString: string;
    entries: Array<{
      address: string;
      value: string;
      wallet?: string;
    }>;
    createdDate: string;
    signedDate?: string;
    confirmedDate?: string;
    comment?: string;
    sequenceId?: string;
  };
}

export interface BitGoWebhookPayload {
  type: string; // "transfer", "transaction", etc.
  coin: string;
  wallet: string;
  transfer?: {
    id: string;
    coin: string;
    wallet: string;
    txid?: string;
    state: string;
    value: string;
    valueString: string;
    entries: Array<{
      address: string;
      value: string;
      wallet?: string;
    }>;
  };
  hash?: string; // transaction hash
  state?: string;
}

export interface WithdrawalObservation {
  provider: string;
  providerRef: string;
  walletId: string;
  txid: string | null;
  state: "SIGNING" | "BROADCAST" | "CONFIRMED" | "FAILED";
  amountAtoms: bigint;
  observedAt: Date;
  raw: any;
}
