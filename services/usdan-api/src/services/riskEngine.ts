import { ApiError } from '../lib/errors.js';
import type { UsdanStore } from '../store/types.js';

export class RiskEngine {
  private readonly purchaseLimitUsd = 100_000;
  private readonly redemptionLimitUsdan = 100_000;

  constructor(private readonly store: UsdanStore) {}

  async assertUserClear(userId: string): Promise<void> {
    const openFlags = await this.store.listOpenComplianceFlags(userId);
    if (openFlags.length > 0) {
      throw new ApiError(403, 'COMPLIANCE_HOLD', 'User has open compliance flags');
    }
  }

  assertPurchaseAmount(amountUsd: number): void {
    if (amountUsd <= 0) throw new ApiError(400, 'BAD_AMOUNT', 'Amount must be positive');
    if (amountUsd > this.purchaseLimitUsd) {
      throw new ApiError(403, 'PURCHASE_LIMIT', 'Purchase amount exceeds configured limit');
    }
  }

  assertRedemptionAmount(amountUsdan: number): void {
    if (amountUsdan <= 0) throw new ApiError(400, 'BAD_AMOUNT', 'Amount must be positive');
    if (amountUsdan > this.redemptionLimitUsdan) {
      throw new ApiError(403, 'REDEMPTION_LIMIT', 'Redemption amount exceeds configured limit');
    }
  }
}
