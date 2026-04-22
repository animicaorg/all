import { createHmac } from 'node:crypto';
import { describe, expect, it } from 'vitest';
import { createTestRuntime } from './setup.js';

describe('webhook ingestion', () => {
  it('applies idempotent settlement updates on valid signatures', async () => {
    const { runtime, config } = createTestRuntime();

    await runtime.store.createUser({ id: 'user_webhook', role: 'USER', email: 'w@example.com' });
    await runtime.services.kyc.setStatus('user_webhook', 'APPROVED', 'manual');
    const bank = await runtime.store.upsertBankAccount({
      userId: 'user_webhook',
      bankAccountHash: 'bank_hash_wh',
      status: 'VERIFIED'
    });

    const intent = await runtime.services.purchase.createIntent({
      userId: 'user_webhook',
      walletAddress: 'anim1webhook',
      bankAccountId: bank.id,
      amountUsd: 10
    });

    const payload = {
      id: 'evt_settled_1',
      type: 'inbound_payment.settled',
      external_reference: intent.id
    };
    const rawBody = JSON.stringify(payload);
    const signature = createHmac('sha256', config.MODERN_TREASURY_WEBHOOK_SECRET).update(rawBody).digest('hex');

    const result = await runtime.services.webhook.processModernTreasuryWebhook(rawBody, `sha256=${signature}`);
    expect(result.accepted).toBe(true);

    const updated = await runtime.store.getPurchaseIntent(intent.id);
    expect(updated?.status).toBe('MINT_AUTHORIZED');

    // Replaying same event should remain idempotent.
    const replay = await runtime.services.webhook.processModernTreasuryWebhook(rawBody, `sha256=${signature}`);
    expect(replay.accepted).toBe(true);

    const deliveries = await runtime.store.listWebhookDeliveries();
    expect(deliveries.length).toBe(1);
    expect(deliveries[0].status).toBe('PROCESSED');
  });
});
