import type { UsdanStore } from '../store/types.js';
import type { ReserveService } from './reserveService.js';

export class AdminService {
  constructor(
    private readonly store: UsdanStore,
    private readonly reserve: ReserveService
  ) {}

  async addComplianceFlag(input: {
    userId: string;
    type: 'SANCTIONS' | 'AML' | 'MANUAL_REVIEW' | 'VELOCITY' | 'LIMIT';
    reason: string;
    actorId: string;
  }) {
    return this.store.createComplianceFlag({
      userId: input.userId,
      type: input.type,
      status: 'OPEN',
      reason: input.reason,
      createdBy: input.actorId
    });
  }

  async recordAdminAction(input: {
    actorId: string;
    action: string;
    targetType: string;
    targetId: string;
    reason?: string;
  }) {
    return this.store.createAdminAction(input);
  }

  async publishReserveSnapshot(actorId: string) {
    const snapshot = await this.reserve.captureSnapshot('MANUAL');
    await this.recordAdminAction({
      actorId,
      action: 'PUBLISH_RESERVE_SNAPSHOT',
      targetType: 'reserve_snapshot',
      targetId: snapshot.id,
      reason: 'manual_publish'
    });
    return snapshot;
  }
}
