import { ApiError } from '../lib/errors.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { UsdanStore } from '../store/types.js';
import type { PurchaseService } from './purchaseService.js';
import type { RedemptionService } from './redemptionService.js';

export class WebhookService {
  constructor(
    private readonly store: UsdanStore,
    private readonly treasury: TreasuryProvider,
    private readonly purchases: PurchaseService,
    private readonly redemptions: RedemptionService
  ) {}

  async processModernTreasuryWebhook(rawBody: string, signature: string): Promise<{ accepted: boolean }> {
    const signatureValid = this.treasury.verifyWebhookSignature(rawBody, signature);
    if (!signatureValid) {
      throw new ApiError(401, 'BAD_WEBHOOK_SIGNATURE', 'Webhook signature validation failed');
    }

    const envelope = this.treasury.parseWebhook(rawBody);
    const existing = await this.store.findWebhookDelivery(envelope.provider, envelope.eventId);
    if (existing?.status === 'PROCESSED') {
      return { accepted: true };
    }

    const delivery = existing
      ? await this.store.updateWebhookDelivery(existing.id, {
          signatureValid: true,
          attemptCount: existing.attemptCount + 1,
          status: 'RECEIVED'
        })
      : await this.store.createWebhookDelivery({
          provider: envelope.provider,
          eventId: envelope.eventId,
          idempotencyKey: envelope.idempotencyKey,
          status: 'RECEIVED',
          signatureValid: true,
          payload: envelope.payload,
          attemptCount: 1
        });

    try {
      await this.routeEvent(envelope.eventType, envelope.payload);
      await this.store.updateWebhookDelivery(delivery.id, {
        status: 'PROCESSED'
      });
    } catch (error) {
      await this.store.updateWebhookDelivery(delivery.id, {
        status: 'FAILED',
        lastError: error instanceof Error ? error.message : String(error)
      });
      throw error;
    }

    return { accepted: true };
  }

  private async routeEvent(eventType: string, payload: Record<string, unknown>): Promise<void> {
    if (eventType.includes('inbound') && eventType.includes('settled')) {
      const purchaseIntentId = String(payload['external_reference'] ?? payload['purchase_intent_id'] ?? '');
      if (purchaseIntentId) {
        await this.purchases.markFundsSettled(purchaseIntentId, String(payload['id'] ?? 'settlement_event'));
      }
      return;
    }

    if (eventType.includes('payout') && eventType.includes('settled')) {
      const redemptionRequestId = String(payload['external_reference'] ?? payload['redemption_request_id'] ?? '');
      if (redemptionRequestId) {
        await this.redemptions.markPayoutSettled(redemptionRequestId, String(payload['id'] ?? 'payout_event'));
      }
    }
  }
}
