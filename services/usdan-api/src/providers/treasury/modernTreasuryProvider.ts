import type { Config } from '../../config.js';
import type { Logger } from '../../logger.js';
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
import { ModernTreasuryClient } from './modernTreasuryClient.js';

export class ModernTreasuryProvider implements TreasuryProvider {
  public readonly name = 'modern_treasury';
  private readonly client: ModernTreasuryClient;

  constructor(
    private readonly config: Config,
    private readonly logger: Logger
  ) {
    this.client = new ModernTreasuryClient(config, logger);
  }

  async createOrGetCustomer(input: TreasuryCustomerInput): Promise<{ customerId: string }> {
    const payload = {
      ledger_account: {
        ledger_id: this.config.MODERN_TREASURY_LEDGER_ID,
        name: input.legalName,
        external_id: input.customerReference,
        metadata: {
          email: input.email ?? ''
        }
      }
    };

    const result = await this.client.request<{ ledger_account: { id: string } }>({
      method: 'POST',
      url: '/api/ledger_accounts',
      data: payload,
      headers: {
        'X-Idempotency-Key': `customer-${input.customerReference}`
      }
    });

    return { customerId: result.ledger_account.id };
  }

  async createInboundFunding(input: TreasuryInboundRequest): Promise<TreasuryInboundResult> {
    const result = await this.client.request<{ expected_payment: { id: string; status: string } }>({
      method: 'POST',
      url: '/api/expected_payments',
      data: {
        expected_payment: {
          amount: input.amountUsd,
          direction: 'credit',
          counterparty_id: input.customerId,
          metadata: {
            external_reference: input.externalReference
          }
        }
      },
      headers: {
        'X-Idempotency-Key': input.idempotencyKey
      }
    });

    return {
      inboundId: result.expected_payment.id,
      status: normalizeStatus(result.expected_payment.status)
    };
  }

  async createPayout(input: TreasuryPayoutRequest): Promise<TreasuryPayoutResult> {
    const result = await this.client.request<{ payment_order: { id: string; status: string } }>({
      method: 'POST',
      url: '/api/payment_orders',
      data: {
        payment_order: {
          amount: input.amountUsd,
          direction: 'debit',
          originating_account_id: this.config.MODERN_TREASURY_PAYOUT_ACCOUNT_ID,
          receiving_account_id: input.destinationBankAccountId,
          metadata: {
            external_reference: input.externalReference,
            customer_id: input.customerId
          }
        }
      },
      headers: {
        'X-Idempotency-Key': input.idempotencyKey
      }
    });

    return {
      payoutId: result.payment_order.id,
      status: normalizePayoutStatus(result.payment_order.status)
    };
  }

  async getLedgerSummary(): Promise<TreasuryLedgerSummary> {
    const result = await this.client.request<{ ledger: { posted_balance: string } }>({
      method: 'GET',
      url: `/api/ledgers/${this.config.MODERN_TREASURY_LEDGER_ID}`
    });

    return {
      settledBalanceUsd: result.ledger.posted_balance,
      pendingInboundUsd: '0',
      pendingPayoutUsd: '0',
      asOfIso: new Date().toISOString()
    };
  }

  verifyWebhookSignature(rawBody: string, signatureHeader: string): boolean {
    const expected = hmacSha256Hex(this.config.MODERN_TREASURY_WEBHOOK_SECRET, rawBody);
    const normalizedHeader = signatureHeader.replace(/^sha256=/, '').trim();
    return safeEqualHex(expected, normalizedHeader);
  }

  parseWebhook(rawBody: string): TreasuryWebhookEnvelope {
    const payload = JSON.parse(rawBody) as Record<string, unknown>;
    const id = String(payload.id ?? payload.event_id ?? 'unknown');
    const type = String(payload.type ?? payload.event_type ?? 'unknown');

    return {
      provider: 'modern_treasury',
      eventId: id,
      eventType: type,
      idempotencyKey: typeof payload['idempotency_key'] === 'string' ? String(payload['idempotency_key']) : undefined,
      occurredAt: typeof payload['created_at'] === 'string' ? String(payload['created_at']) : new Date().toISOString(),
      payload
    };
  }
}

function normalizeStatus(status: string): TreasuryInboundResult['status'] {
  if (status.includes('posted') || status.includes('settled') || status.includes('completed')) return 'settled';
  if (status.includes('fail')) return 'failed';
  return 'pending';
}

function normalizePayoutStatus(status: string): TreasuryPayoutResult['status'] {
  if (status.includes('settled') || status.includes('completed')) return 'settled';
  if (status.includes('sent') || status.includes('processing')) return 'sent';
  if (status.includes('fail')) return 'failed';
  return 'pending';
}
