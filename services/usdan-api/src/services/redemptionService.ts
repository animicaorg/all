import { randomUUID } from 'node:crypto';
import { ApiError } from '../lib/errors.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { RedemptionRequestRecord, UsdanStore } from '../store/types.js';
import type { AuditService } from './auditService.js';
import type { KycService } from './kycService.js';
import type { RiskEngine } from './riskEngine.js';

export class RedemptionService {
  constructor(
    private readonly store: UsdanStore,
    private readonly treasury: TreasuryProvider,
    private readonly kyc: KycService,
    private readonly risk: RiskEngine,
    private readonly audit: AuditService
  ) {}

  async createRequest(input: {
    userId: string;
    walletAddress: string;
    bankAccountId: string;
    amountUsdan: number;
    userIntentHash: string;
  }): Promise<RedemptionRequestRecord> {
    await this.kyc.requireApproved(input.userId);
    await this.risk.assertUserClear(input.userId);
    this.risk.assertRedemptionAmount(input.amountUsdan);

    const bank = await this.store.getBankAccount(input.bankAccountId);
    if (!bank || bank.status !== 'VERIFIED') {
      throw new ApiError(403, 'BANK_ACCOUNT_REQUIRED', 'Verified bank account is required');
    }

    const request = await this.store.createRedemptionRequest({
      userId: input.userId,
      walletAddress: input.walletAddress,
      bankAccountId: input.bankAccountId,
      amountUsdan: input.amountUsdan.toFixed(2),
      status: 'ONCHAIN_PENDING',
      requestNonce: randomUUID(),
      userIntentHash: input.userIntentHash,
      onchainTxHash: undefined,
      payoutReference: undefined,
      cancellationReason: undefined
    });

    await this.audit.log({
      actorType: 'user',
      actorId: input.userId,
      action: 'redemption_requested',
      entityType: 'redemption_request',
      entityId: request.id,
      details: {
        amountUsdan: request.amountUsdan,
        walletAddress: request.walletAddress
      }
    });

    return request;
  }

  async markOnchainConfirmed(requestId: string, onchainTxHash: string): Promise<RedemptionRequestRecord> {
    const request = await this.store.getRedemptionRequest(requestId);
    if (!request) throw new ApiError(404, 'NOT_FOUND', 'Redemption request not found');

    const confirmed = await this.store.updateRedemptionRequest(request.id, {
      status: 'ONCHAIN_CONFIRMED',
      onchainTxHash
    });

    await this.store.createFiatPaymentEvent({
      redemptionRequestId: confirmed.id,
      provider: 'animica',
      eventType: 'LEDGER_ENTRY_POSTED',
      externalId: onchainTxHash,
      payload: { onchainTxHash }
    });

    const customer = await this.treasury.createOrGetCustomer({
      customerReference: confirmed.userId,
      legalName: confirmed.userId
    });

    const payout = await this.treasury.createPayout({
      amountUsd: confirmed.amountUsdan,
      customerId: customer.customerId,
      destinationBankAccountId: confirmed.bankAccountId ?? 'unknown',
      externalReference: confirmed.id,
      idempotencyKey: `redeem:${confirmed.id}`
    });

    await this.store.createFiatPaymentEvent({
      redemptionRequestId: confirmed.id,
      provider: this.treasury.name,
      eventType: 'PAYOUT_CREATED',
      externalId: payout.payoutId,
      payload: { ...payout }
    });

    return this.store.updateRedemptionRequest(confirmed.id, {
      status: payout.status === 'settled' ? 'COMPLETED' : 'PAYOUT_PENDING',
      payoutReference: payout.payoutId
    });
  }

  async markPayoutSettled(requestId: string, payoutReference: string): Promise<RedemptionRequestRecord> {
    const request = await this.store.getRedemptionRequest(requestId);
    if (!request) throw new ApiError(404, 'NOT_FOUND', 'Redemption request not found');

    await this.store.createFiatPaymentEvent({
      redemptionRequestId: request.id,
      provider: this.treasury.name,
      eventType: 'PAYOUT_SETTLED',
      externalId: payoutReference,
      payload: { payoutReference }
    });

    return this.store.updateRedemptionRequest(request.id, {
      status: 'COMPLETED',
      payoutReference
    });
  }
}
