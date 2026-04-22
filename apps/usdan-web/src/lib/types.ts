export interface SessionState {
  token: string;
  userId: string;
  walletAddress: string;
}

export interface KycStatusResponse {
  status: string;
  bankAccounts: Array<{
    id: string;
    status: string;
    bankAccountHash: string;
  }>;
}

export interface BuyIntent {
  id: string;
  amountUsd: string;
  amountUsdan: string;
  status: string;
  walletAddress: string;
  createdAt: string;
  mintTxHash?: string;
}

export interface RedemptionRequest {
  id: string;
  amountUsdan: string;
  status: string;
  walletAddress: string;
  createdAt: string;
  payoutReference?: string;
}

export interface ReserveDashboard {
  tokenSupply: string;
  reserveLedgerBalance: string;
  outstandingRedemptionQueue: string;
  pendingMintQueue: string;
  latestAttestationTimestamp?: string;
  coverageRatioBps: number;
  minCoverageBps: number;
  reconciliationHash: string;
}
