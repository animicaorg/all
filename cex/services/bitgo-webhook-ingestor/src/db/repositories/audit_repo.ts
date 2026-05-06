/**
 * Audit Logs Repository
 */

import type { PoolClient } from "pg";

export interface AuditLogEntry {
  eventType: string;
  resourceType: string;
  resourceId: string;
  userId?: string;
  actorId?: string;
  actorType: "SYSTEM" | "ADMIN" | "USER";
  changes: Record<string, unknown>;
  metadata: Record<string, unknown>;
  ipAddress?: string;
}

export class AuditRepo {
  constructor(private client: PoolClient) {}

  /**
   * Create audit log entry
   */
  async log(entry: AuditLogEntry): Promise<void> {
    const query = `
      INSERT INTO audit_logs (
        event_type, resource_type, resource_id,
        user_id, actor_id, actor_type,
        action, entity_type, entity_id,
        changes, metadata, ip_address
      ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
    `;

    await this.client.query(query, [
      entry.eventType,
      entry.resourceType,
      entry.resourceId,
      entry.userId || null,
      entry.actorId || null,
      entry.actorType,
      entry.eventType,
      entry.resourceType,
      entry.resourceId,
      JSON.stringify(entry.changes),
      JSON.stringify(entry.metadata),
      entry.ipAddress || null,
    ]);
  }

  /**
   * Log deposit event
   */
  async logDeposit(
    eventType: string,
    depositId: string,
    userId: string | null,
    changes: Record<string, unknown>,
    metadata: Record<string, unknown> = {}
  ): Promise<void> {
    await this.log({
      eventType,
      resourceType: "DEPOSIT",
      resourceId: depositId,
      userId: userId || undefined,
      actorType: "SYSTEM",
      changes,
      metadata,
    });
  }
}
