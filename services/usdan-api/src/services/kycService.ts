import { ApiError } from '../lib/errors.js';
import type { UsdanStore, KycStatus } from '../store/types.js';

export class KycService {
  constructor(private readonly store: UsdanStore) {}

  async getStatus(userId: string): Promise<KycStatus> {
    const record = await this.store.getLatestKyc(userId);
    return record?.status ?? 'NOT_STARTED';
  }

  async requireApproved(userId: string): Promise<void> {
    const status = await this.getStatus(userId);
    if (status !== 'APPROVED') {
      throw new ApiError(403, 'KYC_REQUIRED', `KYC not approved: ${status}`);
    }
  }

  async setStatus(userId: string, status: KycStatus, provider: string): Promise<KycStatus> {
    const rec = await this.store.upsertKycRecord({ userId, status, provider });
    return rec.status;
  }
}
