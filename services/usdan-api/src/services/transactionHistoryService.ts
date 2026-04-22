import type { UsdanStore } from '../store/types.js';

export class TransactionHistoryService {
  constructor(private readonly store: UsdanStore) {}

  async listUserTransactions(userId: string) {
    const [purchases, redemptions] = await Promise.all([
      this.store.listPurchaseIntents(userId),
      this.store.listRedemptionRequests(userId)
    ]);

    const purchaseEvents = purchases.map((purchase) => ({
      type: 'buy',
      id: purchase.id,
      amount: purchase.amountUsdan,
      status: purchase.status,
      createdAt: purchase.createdAt,
      walletAddress: purchase.walletAddress,
      txHash: purchase.mintTxHash
    }));

    const redemptionEvents = redemptions.map((redemption) => ({
      type: 'redeem',
      id: redemption.id,
      amount: redemption.amountUsdan,
      status: redemption.status,
      createdAt: redemption.createdAt,
      walletAddress: redemption.walletAddress,
      txHash: redemption.onchainTxHash,
      payoutReference: redemption.payoutReference
    }));

    return [...purchaseEvents, ...redemptionEvents].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }
}
