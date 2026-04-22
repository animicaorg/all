import { describe, expect, it } from 'vitest';
import { createTestRuntime } from './setup.js';

describe('buy + redeem e2e', () => {
  it('runs full buy and redeem lifecycle through backend services', async () => {
    const { runtime } = createTestRuntime();

    await runtime.store.createUser({ id: 'user_1', role: 'USER', email: 'user@example.com' });
    await runtime.services.kyc.setStatus('user_1', 'APPROVED', 'manual');
    const bank = await runtime.store.upsertBankAccount({
      userId: 'user_1',
      bankAccountHash: 'bank_hash_1',
      status: 'VERIFIED'
    });

    await runtime.services.walletBinding.bindWallet({
      userId: 'user_1',
      walletAddress: 'anim1wallet1',
      chainId: 1337,
      message: 'Sign in to USDAN',
      signature: '0xabc123',
      isPrimary: true
    });

    const intent = await runtime.services.purchase.createIntent({
      userId: 'user_1',
      walletAddress: 'anim1wallet1',
      bankAccountId: bank.id,
      amountUsd: 250
    });
    expect(intent.status).toBe('FUNDS_PENDING');

    const settled = await runtime.services.purchase.markFundsSettled(intent.id, 'stl_1');
    expect(settled.status).toBe('MINT_AUTHORIZED');

    const submitted = await runtime.services.purchase.markMintSubmitted(intent.id, '0xmintsubmitted');
    expect(submitted.status).toBe('MINT_SUBMITTED');

    const confirmed = await runtime.services.purchase.markMintConfirmed(intent.id, '0xmintconfirmed');
    expect(confirmed.status).toBe('MINT_CONFIRMED');

    const redemption = await runtime.services.redemption.createRequest({
      userId: 'user_1',
      walletAddress: 'anim1wallet1',
      bankAccountId: bank.id,
      amountUsdan: 100,
      userIntentHash: 'intent_hash_1'
    });
    expect(redemption.status).toBe('ONCHAIN_PENDING');

    const onchain = await runtime.services.redemption.markOnchainConfirmed(redemption.id, '0xredeemonchain');
    expect(onchain.status).toBe('PAYOUT_PENDING');

    const completed = await runtime.services.redemption.markPayoutSettled(redemption.id, 'payout-123');
    expect(completed.status).toBe('COMPLETED');

    const txHistory = await runtime.services.transactions.listUserTransactions('user_1');
    expect(txHistory.length).toBeGreaterThanOrEqual(2);
  });
});
