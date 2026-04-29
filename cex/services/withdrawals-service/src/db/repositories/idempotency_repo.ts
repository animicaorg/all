/**
 * Idempotency Repository
 */

import type { PoolClient } from "pg";

export interface IdempotencyRecord {
  id: string;
  idempotencyKey: string;
  userId: string;
  endpoint: string;
  withdrawalId: string;
  requestBody: any;
  responseBody: any;
  responseStatus: number;
  createdAt: Date;
  expiresAt: Date;
}

export class IdempotencyRepo {
  constructor(private client: PoolClient) {}

  async check(
    idempotencyKey: string,
    userId: string,
    endpoint: string
  ): Promise<IdempotencyRecord | null> {
    const result = await this.client.query(
      `SELECT * FROM withdrawal_idempotency 
       WHERE idempotency_key = $1 
         AND user_id = $2 
         AND endpoint = $3
         AND expires_at > NOW()`,
      [idempotencyKey, userId, endpoint]
    );
    return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
  }

  async record(
    idempotencyKey: string,
    userId: string,
    endpoint: string,
    withdrawalId: string,
    requestBody: any,
    responseBody: any,
    responseStatus: number,
    expiresInHours: number = 24
  ): Promise<IdempotencyRecord> {
    const query = `
      INSERT INTO withdrawal_idempotency (
        idempotency_key, user_id, endpoint, withdrawal_id,
        request_body, response_body, response_status, expires_at
      ) VALUES (
        $1, $2, $3, $4, $5, $6, $7, NOW() + INTERVAL '1 hour' * $8
      )
      RETURNING *
    `;

    const values = [
      idempotencyKey,
      userId,
      endpoint,
      withdrawalId,
      JSON.stringify(requestBody),
      JSON.stringify(responseBody),
      responseStatus,
      expiresInHours,
    ];

    const result = await this.client.query(query, values);
    return this.mapRow(result.rows[0]);
  }

  private mapRow(row: any): IdempotencyRecord {
    return {
      id: row.id,
      idempotencyKey: row.idempotency_key,
      userId: row.user_id,
      endpoint: row.endpoint,
      withdrawalId: row.withdrawal_id,
      requestBody: row.request_body,
      responseBody: row.response_body,
      responseStatus: row.response_status,
      createdAt: row.created_at,
      expiresAt: row.expires_at,
    };
  }
}
