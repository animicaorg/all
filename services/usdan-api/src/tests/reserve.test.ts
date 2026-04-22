import { describe, expect, it } from 'vitest';
import { createTestRuntime } from './setup.js';

describe('reserve dashboard', () => {
  it('derives reserve metrics from on-chain + off-chain states', async () => {
    const { runtime } = createTestRuntime();

    runtime.services.chain.setSupply('1000.00');

    await runtime.store.createPurchaseIntent({
      userId: 'u1',
      walletAddress: 'anim1u1',
      amountUsd: '200.00',
      amountUsdan: '200.00',
      status: 'MINT_AUTHORIZED',
      requestId: 'req-pending-1',
      nonce: 'nonce-pending-1',
      bankAccountId: undefined,
      modernTreasuryRef: undefined,
      settlementReference: undefined,
      mintTxHash: undefined
    });

    await runtime.store.createRedemptionRequest({
      userId: 'u2',
      walletAddress: 'anim1u2',
      amountUsdan: '50.00',
      status: 'PAYOUT_PENDING',
      requestNonce: 'redeem-nonce',
      bankAccountId: undefined,
      userIntentHash: undefined,
      onchainTxHash: undefined,
      payoutReference: undefined,
      cancellationReason: undefined
    });

    const dashboard = await runtime.services.reserve.getDashboard();

    expect(dashboard.tokenSupply).toBe('1000.00');
    expect(Number(dashboard.pendingMintQueue)).toBeGreaterThanOrEqual(200);
    expect(Number(dashboard.outstandingRedemptionQueue)).toBeGreaterThanOrEqual(50);
    expect(typeof dashboard.coverageRatioBps).toBe('number');
    expect(typeof dashboard.reconciliationHash).toBe('string');
  });
});
