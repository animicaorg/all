/**
 * Deposit Ingestion Pipeline
 * 
 * Processes normalized deposit observations:
 * - Resolves address ownership
 * - Upserts deposit records idempotently
 * - Applies risk checks
 * - Logs audit trail
 */

import type { Pool, PoolClient } from "pg";
import type { Logger } from "pino";
import type { DepositObservation } from "../bitgo/types.js";
import {
  DepositsRepo,
  AddressesRepo,
  AuditRepo,
  NetworksRepo,
  OutboxRepo,
} from "../db/repositories/index.js";
import { runRiskChecks } from "./risk.js";

export interface IngestResult {
  depositId: string;
  status: string;
  isNew: boolean;
  userId: string | null;
  unassigned: boolean;
  riskHold: boolean;
}

/**
 * Ingest a deposit observation
 */
export async function ingestDeposit(
  pool: Pool,
  observation: DepositObservation,
  logger: Logger
): Promise<IngestResult> {
  const client = await pool.connect();
  
  try {
    await client.query("BEGIN");

    // Initialize repositories
    const depositsRepo = new DepositsRepo(client);
    const addressesRepo = new AddressesRepo(client);
    const auditRepo = new AuditRepo(client);
    const networksRepo = new NetworksRepo(client);
    const outboxRepo = new OutboxRepo(client);

    // Get asset network ID (already looked up in normalization)
    const assetNetwork = await findAssetNetwork(
      client,
      observation.assetSymbol,
      observation.networkCode,
      observation.raw.tokenContractAddress
    );

    if (!assetNetwork) {
      logger.error(
        {
          assetSymbol: observation.assetSymbol,
          networkCode: observation.networkCode,
        },
        "Asset network not found during ingestion"
      );
      await client.query("ROLLBACK");
      throw new Error("Asset network not found");
    }

    // Resolve user by address
    const userId = await addressesRepo.findUserByAddress(
      assetNetwork.id,
      observation.address,
      observation.tag
    );

    if (!userId) {
      logger.warn(
        {
          address: observation.address,
          tag: observation.tag,
          assetSymbol: observation.assetSymbol,
        },
        "No user mapping found for deposit address"
      );
    }

    // Get confirmations required
    const confirmationsRequired = await networksRepo.getConfirmationsRequired(
      assetNetwork.id
    );

    // Upsert deposit
    const deposit = await depositsRepo.upsert(
      observation,
      assetNetwork.id,
      userId,
      confirmationsRequired
    );

    const isNew = deposit.createdAt.getTime() === deposit.updatedAt.getTime();

    // Run risk checks
    const riskResult = await runRiskChecks(deposit, client, logger);
    
    if (!riskResult.ok && riskResult.hold) {
      await depositsRepo.setRiskHold(deposit.id, riskResult.reason || "Risk check failed");
      logger.warn(
        { depositId: deposit.id, reason: riskResult.reason, flags: riskResult.flags },
        "Deposit held by risk check"
      );
    }

    // Log audit event
    const eventType = isNew ? "DEPOSIT_DETECTED" : "DEPOSIT_UPDATED";
    await auditRepo.logDeposit(
      eventType,
      deposit.id,
      userId,
      {
        txid: observation.txid,
        address: observation.address,
        amountAtoms: observation.amountAtoms.toString(),
        confirmations: observation.confirmations,
        status: deposit.status,
      },
      {
        providerEventId: observation.providerEventId,
        transferId: observation.transferId,
      }
    );

    // If deposit just became CONFIRMED and has user, create outbox entry
    if (
      deposit.status === "CONFIRMED" &&
      deposit.userId &&
      !deposit.riskHold &&
      !deposit.unassigned
    ) {
      const assetSymbol = await networksRepo.getAssetSymbol(assetNetwork.id);
      if (assetSymbol) {
        await outboxRepo.create(
          deposit.id,
          deposit.userId,
          assetSymbol,
          deposit.amountAtoms,
          {
            provider: observation.provider,
            txid: observation.txid,
            address: observation.address,
            transferId: observation.transferId,
            coin: observation.coin,
            network: observation.networkCode,
          }
        );

        await auditRepo.logDeposit(
          "DEPOSIT_CONFIRMED",
          deposit.id,
          deposit.userId,
          {
            confirmations: deposit.confirmations,
            confirmationsRequired: deposit.confirmationsRequired,
          },
          {}
        );
      }
    }

    // Update address last used timestamp
    if (userId) {
      await addressesRepo.updateLastUsed(
        assetNetwork.id,
        observation.address,
        observation.tag
      );
    }

    await client.query("COMMIT");

    return {
      depositId: deposit.id,
      status: deposit.status,
      isNew,
      userId,
      unassigned: deposit.unassigned,
      riskHold: deposit.riskHold,
    };
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Helper to find asset network
 */
async function findAssetNetwork(
  client: PoolClient,
  assetSymbol: string,
  networkCode: string,
  contractAddress?: string
): Promise<{ id: string } | null> {
  const query = `
    SELECT an.id
    FROM asset_networks an
    JOIN assets a ON a.id = an.asset_id
    JOIN networks n ON n.id = an.network_id
    WHERE a.symbol = $1
      AND n.code = $2
      AND (an.contract_address = $3 OR (an.contract_address IS NULL AND $3 IS NULL))
      AND an.deposits_enabled = true
  `;

  const result = await client.query(query, [
    assetSymbol,
    networkCode,
    contractAddress || null,
  ]);

  return result.rows.length > 0 ? { id: result.rows[0].id } : null;
}
