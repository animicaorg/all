export interface TreasuryCustomerInput {
  customerReference: string;
  legalName: string;
  email?: string;
}

export interface TreasuryInboundRequest {
  amountUsd: string;
  customerId: string;
  externalReference: string;
  idempotencyKey: string;
}

export interface TreasuryInboundResult {
  inboundId: string;
  status: 'pending' | 'posted' | 'settled' | 'failed';
  ledgerTransactionId?: string;
}

export interface TreasuryPayoutRequest {
  amountUsd: string;
  customerId: string;
  destinationBankAccountId: string;
  externalReference: string;
  idempotencyKey: string;
}

export interface TreasuryPayoutResult {
  payoutId: string;
  status: 'pending' | 'sent' | 'settled' | 'failed';
  ledgerTransactionId?: string;
}

export interface TreasuryLedgerSummary {
  settledBalanceUsd: string;
  pendingInboundUsd: string;
  pendingPayoutUsd: string;
  asOfIso: string;
}

export interface TreasuryWebhookEnvelope {
  provider: 'modern_treasury';
  eventId: string;
  eventType: string;
  idempotencyKey?: string;
  occurredAt: string;
  payload: Record<string, unknown>;
}
