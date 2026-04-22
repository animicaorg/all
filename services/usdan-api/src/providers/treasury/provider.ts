import type {
  TreasuryCustomerInput,
  TreasuryInboundRequest,
  TreasuryInboundResult,
  TreasuryLedgerSummary,
  TreasuryPayoutRequest,
  TreasuryPayoutResult,
  TreasuryWebhookEnvelope
} from './types.js';

export interface TreasuryProvider {
  readonly name: 'modern_treasury' | string;

  createOrGetCustomer(input: TreasuryCustomerInput): Promise<{ customerId: string }>;
  createInboundFunding(input: TreasuryInboundRequest): Promise<TreasuryInboundResult>;
  createPayout(input: TreasuryPayoutRequest): Promise<TreasuryPayoutResult>;
  getLedgerSummary(): Promise<TreasuryLedgerSummary>;

  verifyWebhookSignature(rawBody: string, signatureHeader: string): boolean;
  parseWebhook(rawBody: string): TreasuryWebhookEnvelope;
}
