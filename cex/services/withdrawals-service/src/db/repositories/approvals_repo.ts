/**
 * Approvals Repository
 */

import type { PoolClient } from "pg";

export interface WithdrawalApproval {
  id: string;
  withdrawalId: string;
  approverId: string;
  approverRole: string;
  action: "APPROVE" | "REJECT";
  reason: string | null;
  metadata: any;
  createdAt: Date;
}

export interface CreateApprovalParams {
  withdrawalId: string;
  approverId: string;
  approverRole: string;
  action: "APPROVE" | "REJECT";
  reason?: string;
  metadata?: any;
}

export class ApprovalsRepo {
  constructor(private client: PoolClient) {}

  async create(params: CreateApprovalParams): Promise<WithdrawalApproval> {
    const query = `
      INSERT INTO withdrawal_approvals (
        withdrawal_id, approver_id, approver_role, action, reason, metadata
      ) VALUES (
        $1, $2, $3, $4, $5, $6
      )
      RETURNING *
    `;

    const values = [
      params.withdrawalId,
      params.approverId,
      params.approverRole,
      params.action,
      params.reason || null,
      JSON.stringify(params.metadata || {}),
    ];

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  async countApprovals(withdrawalId: string): Promise<number> {
    const result = await this.client.query(
      `SELECT COUNT(*) as count 
       FROM withdrawal_approvals 
       WHERE withdrawal_id = $1 AND action = 'APPROVE'`,
      [withdrawalId]
    );
    return parseInt(result.rows[0].count, 10);
  }

  async listByWithdrawal(withdrawalId: string): Promise<WithdrawalApproval[]> {
    const result = await this.client.query(
      `SELECT * FROM withdrawal_approvals 
       WHERE withdrawal_id = $1 
       ORDER BY created_at ASC`,
      [withdrawalId]
    );
    return result.rows.map(this.mapRow);
  }

  async hasApproved(withdrawalId: string, approverId: string): Promise<boolean> {
    const result = await this.client.query(
      `SELECT 1 FROM withdrawal_approvals 
       WHERE withdrawal_id = $1 AND approver_id = $2`,
      [withdrawalId, approverId]
    );
    return result.rows.length > 0;
  }

  private mapRow(row: any): WithdrawalApproval {
    return {
      id: row.id,
      withdrawalId: row.withdrawal_id,
      approverId: row.approver_id,
      approverRole: row.approver_role,
      action: row.action,
      reason: row.reason,
      metadata: row.metadata,
      createdAt: row.created_at,
    };
  }
}
