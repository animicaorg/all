/**
 * Audit Repository
 */

import type { PoolClient } from "pg";

export interface AuditLogEntry {
  id: string;
  eventType: string;
  withdrawalId: string;
  userId: string | null;
  actorId: string | null;
  actorType: "SYSTEM" | "ADMIN" | "USER";
  changes: any;
  metadata: any;
  ipAddress: string | null;
  createdAt: Date;
}

export interface LogAuditParams {
  eventType: string;
  withdrawalId: string;
  userId?: string;
  actorId?: string;
  actorType?: "SYSTEM" | "ADMIN" | "USER";
  changes?: any;
  metadata?: any;
  ipAddress?: string;
}

export class AuditRepo {
  constructor(private client: PoolClient) {}

  async log(params: LogAuditParams): Promise<AuditLogEntry> {
    const query = `
      INSERT INTO withdrawal_audit_log (
        event_type, withdrawal_id, user_id, actor_id, actor_type,
        changes, metadata, ip_address
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, $8
      )
      RETURNING *
    `;

    const values = [
      params.eventType,
      params.withdrawalId,
      params.userId || null,
      params.actorId || null,
      params.actorType || "SYSTEM",
      JSON.stringify(params.changes || {}),
      JSON.stringify(params.metadata || {}),
      params.ipAddress || null,
    ];

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  async listByWithdrawal(
    withdrawalId: string,
    limit: number = 100
  ): Promise<AuditLogEntry[]> {
    const result = await this.client.query(
      `SELECT * FROM withdrawal_audit_log 
       WHERE withdrawal_id = $1 
       ORDER BY created_at DESC 
       LIMIT $2`,
      [withdrawalId, limit]
    );
    return result.rows.map(this.mapRow);
  }

  private mapRow(row: any): AuditLogEntry {
    return {
      id: row.id,
      eventType: row.event_type,
      withdrawalId: row.withdrawal_id,
      userId: row.user_id,
      actorId: row.actor_id,
      actorType: row.actor_type,
      changes: row.changes,
      metadata: row.metadata,
      ipAddress: row.ip_address,
      createdAt: row.created_at,
    };
  }
}
