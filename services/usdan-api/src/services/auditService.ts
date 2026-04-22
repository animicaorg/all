import type { UsdanStore } from '../store/types.js';

export class AuditService {
  constructor(private readonly store: UsdanStore) {}

  async log(input: {
    actorType: string;
    actorId: string;
    action: string;
    entityType: string;
    entityId: string;
    requestId?: string;
    idempotencyKey?: string;
    details?: Record<string, unknown>;
  }): Promise<void> {
    await this.store.createAuditLog({
      actorType: input.actorType,
      actorId: input.actorId,
      action: input.action,
      entityType: input.entityType,
      entityId: input.entityId,
      requestId: input.requestId,
      idempotencyKey: input.idempotencyKey,
      details: input.details
    });
  }
}
