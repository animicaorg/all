/**
 * Deposit Address Assignment
 * 
 * Creates and assigns deposit addresses to users
 */

import type { Pool } from "pg";
import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { AddressesRepository } from "../db/repositories/addresses_repo.js";

export interface AssignAddressParams {
  user_id: string;
  asset_network_id: string;
  label?: string;
}

export interface AssignedAddress {
  address: string;
  label: string;
  user_id: string;
  created: boolean; // true if newly created, false if reused
}

/**
 * Assign a deposit address to a user
 * 
 * Reuses existing address if available, otherwise creates new one
 */
export async function assignDepositAddress(
  params: AssignAddressParams,
  pool: Pool,
  rpcClient: AnimicaRpcClient,
  logger: Logger
): Promise<AssignedAddress> {
  const addressesRepo = new AddressesRepository(pool, logger);
  
  logger.debug(
    { user_id: params.user_id, asset_network_id: params.asset_network_id },
    "Assigning deposit address"
  );
  
  // Create new address via RPC
  const label = params.label || `user_${params.user_id}_${Date.now()}`;
  
  try {
    const address = await rpcClient.createAddress(label);
    
    logger.info(
      { user_id: params.user_id, address, label },
      "Created new deposit address"
    );
    
    // Store in database (will return existing if user already has one)
    const depositAddress = await addressesRepo.getOrCreate(
      params.user_id,
      params.asset_network_id,
      "ANIMICA_NODE", // wallet_id
      address,
      null // no tag for Animica
    );
    
    const created = depositAddress.address === address;
    
    return {
      address: depositAddress.address,
      label: depositAddress.label || label,
      user_id: params.user_id,
      created,
    };
  } catch (error: any) {
    logger.error(
      { error, user_id: params.user_id },
      "Failed to create deposit address"
    );
    throw new Error(`Failed to create deposit address: ${error.message}`);
  }
}

/**
 * Get deposit address for a user (without creating new one)
 */
export async function getDepositAddress(
  userId: string,
  assetNetworkId: string,
  pool: Pool,
  logger: Logger
): Promise<string | null> {
  const addressesRepo = new AddressesRepository(pool, logger);
  
  const query = `
    SELECT address FROM user_deposit_addresses
    WHERE user_id = $1 AND asset_network_id = $2 AND status = 'ACTIVE'
    LIMIT 1
  `;
  
  const result = await pool.query(query, [userId, assetNetworkId]);
  
  if (result.rows.length > 0) {
    const address = result.rows[0].address;
    logger.debug(
      { user_id: userId, address },
      "Found existing deposit address"
    );
    return address;
  }
  
  return null;
}
