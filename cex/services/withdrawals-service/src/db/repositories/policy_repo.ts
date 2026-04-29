/**
 * Policy Repository
 */

import type { PoolClient } from "pg";

export interface WithdrawalPolicy {
  id: string;
  assetNetworkId: string;
  minWithdrawalAtoms: bigint;
  maxWithdrawalAtoms: bigint | null;
  dailyLimitAtoms: bigint | null;
  dailyLimitCount: number | null;
  kycTierRequired: string[];
  requiredApprovals: number;
  highRiskThresholdAtoms: bigint | null;
  highRiskApprovals: number;
  whitelistOnly: boolean;
  enabled: boolean;
  metadata: any;
  createdAt: Date;
  updatedAt: Date;
}

export class PolicyRepo {
  constructor(private client: PoolClient) {}

  async getByAssetNetwork(assetNetworkId: string): Promise<WithdrawalPolicy | null> {
    const result = await this.client.query(
      "SELECT * FROM withdrawal_policies WHERE asset_network_id = $1",
      [assetNetworkId]
    );
    return result.rows.length > 0 ? this.mapRow(result.rows[0]) : null;
  }

  async list(enabledOnly: boolean = true): Promise<WithdrawalPolicy[]> {
    const query = enabledOnly
      ? "SELECT * FROM withdrawal_policies WHERE enabled = true ORDER BY created_at DESC"
      : "SELECT * FROM withdrawal_policies ORDER BY created_at DESC";

    const result = await this.client.query(query);
    return result.rows.map(this.mapRow);
  }

  private mapRow(row: any): WithdrawalPolicy {
    return {
      id: row.id,
      assetNetworkId: row.asset_network_id,
      minWithdrawalAtoms: BigInt(row.min_withdrawal_atoms),
      maxWithdrawalAtoms: row.max_withdrawal_atoms ? BigInt(row.max_withdrawal_atoms) : null,
      dailyLimitAtoms: row.daily_limit_atoms ? BigInt(row.daily_limit_atoms) : null,
      dailyLimitCount: row.daily_limit_count,
      kycTierRequired: row.kyc_tier_required || [],
      requiredApprovals: row.required_approvals,
      highRiskThresholdAtoms: row.high_risk_threshold_atoms ? BigInt(row.high_risk_threshold_atoms) : null,
      highRiskApprovals: row.high_risk_approvals,
      whitelistOnly: row.whitelist_only,
      enabled: row.enabled,
      metadata: row.metadata,
      createdAt: row.created_at,
      updatedAt: row.updated_at,
    };
  }
}
