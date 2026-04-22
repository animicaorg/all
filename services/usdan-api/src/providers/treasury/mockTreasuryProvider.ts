import { hmacSha256Hex, safeEqualHex } from '../../lib/crypto.js';
import type { TreasuryProvider } from './provider.js';
import type {
  TreasuryCustomerInput,
  TreasuryInboundRequest,
  TreasuryInboundResult,
  TreasuryLedgerSummary,
  TreasuryPayoutRequest,
  TreasuryPayoutResult,
  TreasuryWebhookEnvelope
} from './types.js';

export class MockTreasuryProvider implements TreasuryProvider {
  public readonly name = 'modern_treasury';
  private readonly customers = new Map<string, string>();

  constructor(private readonly webhookSecret = 'test-secret') {}

  async createOrGetCustomer(input: TreasuryCustomerInput): Promise<{ customerId: string }> {
    if (!this.customers.has(input.customerReference)) {
      this.customers.set(input.customerReference, `cust_${this.customers.size + 1}`);
    }
    return { customerId: this.customers.get(input.customerReference)! };
  }

  async createInboundFunding(input: TreasuryInboundRequest): Promise<TreasuryInboundResult> {
    return {
      inboundId: `inbound_${input.idempotencyKey}`,
      status: 'pending'
    };
  }

  async createPayout(input: TreasuryPayoutRequest): Promise<TreasuryPayoutResult> {
    return {
      payoutId: `payout_${input.idempotencyKey}`,
      status: 'pending'
    };
  }

  async getLedgerSummary(): Promise<TreasuryLedgerSummary> {
    return {
      settledBalanceUsd: '1000000.00',
      pendingInboundUsd: '0.00',
      pendingPayoutUsd: '0.00',
      asOfIso: new Date().toISOString()
    };
  }

  verifyWebhookSignature(rawBody: string, signatureHeader: string): boolean {
    const expected = hmacSha256Hex(this.webhookSecret, rawBody);
    const normalizedHeader = signatureHeader.replace(/^sha256=/, '').trim();
    return safeEqualHex(expected, normalizedHeader);
  }

  parseWebhook(rawBody: string): TreasuryWebhookEnvelope {
    const payload = JSON.parse(rawBody) as Record<string, unknown>;
    return {
      provider: 'modern_treasury',
      eventId: String(payload.id ?? 'event_unknown'),
      eventType: String(payload.type ?? 'unknown'),
      occurredAt: new Date().toISOString(),
      payload
    };
  }
}
