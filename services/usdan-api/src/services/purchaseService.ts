import { randomUUID } from 'node:crypto';
import { ApiError } from '../lib/errors.js';
import type { TreasuryProvider } from '../providers/treasury/provider.js';
import type { PurchaseIntentRecord, UsdanStore } from '../store/types.js';
import type { AuditService } from './auditService.js';
import type { KycService } from './kycService.js';
import type { MintAuthorizationService } from './mintAuthorizationService.js';
import type { RiskEngine } from './riskEngine.js';

export class PurchaseService {
  constructor(
    private readonly store: UsdanStore,
    private readonly treasury: TreasuryProvider,
    private readonly kyc: KycService,
    private readonly risk: RiskEngine,
    private readonly mintAuth: MintAuthorizationService,
    private readonly audit: AuditService
  ) {}

  async createIntent(input: {
    userId: string;
    walletAddress: string;
    bankAccountId: string;
    amountUsd: number;
  }): Promise<PurchaseIntentRecord> {
    await this.kyc.requireApproved(input.userId);
    await this.risk.assertUserClear(input.userId);
    this.risk.assertPurchaseAmount(input.amountUsd);

    const bank = await this.store.getBankAccount(input.bankAccountId);
    if (!bank || bank.status !== 'VERIFIED') {
      throw new ApiError(403, 'BANK_ACCOUNT_REQUIRED', 'Verified bank account is required');
    }

    const requestId = `buy_${randomUUID()}`;
    const nonce = randomUUID();

    const intent = await this.store.createPurchaseIntent({
      userId: input.userId,
      walletAddress: input.walletAddress,
      bankAccountId: input.bankAccountId,
      amountUsd: input.amountUsd.toFixed(2),
      amountUsdan: input.amountUsd.toFixed(2),
      status: 'FUNDS_PENDING',
      modernTreasuryRef: undefined,
      settlementReference: undefined,
      requestId,
      nonce,
      mintTxHash: undefined
    });

    const customer = await this.treasury.createOrGetCustomer({
      customerReference: input.userId,
      legalName: input.userId
    });

    const inbound = await this.treasury.createInboundFunding({
      amountUsd: input.amountUsd.toFixed(2),
      customerId: customer.customerId,
      externalReference: intent.id,
      idempotencyKey: `buy:${intent.id}`
    });

    const next = await this.store.updatePurchaseIntent(intent.id, {
      modernTreasuryRef: inbound.inboundId,
      status: inbound.status === 'settled' ? 'FUNDS_SETTLED' : 'FUNDS_PENDING'
    });

    await this.store.createFiatPaymentEvent({
      purchaseIntentId: intent.id,
      eventType: 'RECEIVABLE_CREATED',
      provider: this.treasury.name,
      externalId: inbound.inboundId,
      payload: { ...inbound }
    });

    await this.audit.log({
      actorType: 'user',
      actorId: input.userId,
      action: 'purchase_intent_created',
      entityType: 'purchase_intent',
      entityId: next.id,
      details: { amountUsd: input.amountUsd, walletAddress: input.walletAddress }
    });

    return next;
  }

  async markFundsSettled(intentId: string, settlementReference: string): Promise<PurchaseIntentRecord> {
    const intent = await this.store.getPurchaseIntent(intentId);
    if (!intent) throw new ApiError(404, 'NOT_FOUND', 'Purchase intent not found');
    if (intent.status === 'MINT_CONFIRMED') return intent;

    const settled = await this.store.updatePurchaseIntent(intent.id, {
      status: 'FUNDS_SETTLED',
      settlementReference
    });

    await this.store.createFiatPaymentEvent({
      purchaseIntentId: settled.id,
      eventType: 'INBOUND_PAYMENT_SETTLED',
      provider: this.treasury.name,
      externalId: settlementReference,
      payload: { settlementReference }
    });

    const prepared = await this.mintAuth.prepare(
      settled.id,
      settled.userId,
      settled.walletAddress,
      settled.amountUsdan
    );
    await this.mintAuth.sign(prepared);

    await this.store.updatePurchaseIntent(settled.id, {
      status: 'MINT_AUTHORIZED'
    });

    return this.store.getPurchaseIntent(intent.id) as Promise<PurchaseIntentRecord>;
  }

  async markMintSubmitted(intentId: string, txHash: string): Promise<PurchaseIntentRecord> {
    const intent = await this.store.getPurchaseIntent(intentId);
    if (!intent) throw new ApiError(404, 'NOT_FOUND', 'Purchase intent not found');

    const auth = await this.store.getMintAuthorizationByPurchase(intent.id);
    if (!auth) throw new ApiError(409, 'MINT_AUTH_MISSING', 'Mint authorization has not been prepared');

    await this.mintAuth.markSubmitted(auth.id, txHash);
    return this.store.updatePurchaseIntent(intent.id, {
      status: 'MINT_SUBMITTED',
      mintTxHash: txHash
    });
  }

  async markMintConfirmed(intentId: string, txHash: string): Promise<PurchaseIntentRecord> {
    const intent = await this.store.getPurchaseIntent(intentId);
    if (!intent) throw new ApiError(404, 'NOT_FOUND', 'Purchase intent not found');

    const auth = await this.store.getMintAuthorizationByPurchase(intent.id);
    if (!auth) throw new ApiError(409, 'MINT_AUTH_MISSING', 'Mint authorization has not been prepared');

    await this.mintAuth.markConfirmed(auth.id, txHash);
    return this.store.updatePurchaseIntent(intent.id, {
      status: 'MINT_CONFIRMED',
      mintTxHash: txHash
    });
  }
}
