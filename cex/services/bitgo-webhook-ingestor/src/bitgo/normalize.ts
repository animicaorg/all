/**
 * BitGo Webhook Normalization
 * 
 * Normalizes various BitGo webhook formats into a unified DepositObservation
 */

import type { Logger } from "pino";
import type { PoolClient } from "pg";
import type { BitGoWebhookPayload, DepositObservation } from "./types.js";

/**
 * Coin to network mapping
 */
const COIN_TO_NETWORK: Record<string, string> = {
  btc: "BTC",
  tbtc: "BTC", // testnet
  eth: "ETH",
  teth: "ETH_SEPOLIA",
  gteth: "ETH", // goerli
  sol: "SOL",
  tsol: "SOL",
};

/**
 * Coin to asset mapping (for native assets)
 */
const COIN_TO_ASSET: Record<string, string> = {
  btc: "BTC",
  tbtc: "BTC",
  eth: "ETH",
  teth: "ETH",
  gteth: "ETH",
  sol: "SOL",
  tsol: "SOL",
};

/**
 * Parse BitGo coin identifier for ERC20 tokens
 * e.g., "erc20:usdt" -> { network: "ETH", token: "usdt" }
 */
function parseBitGoCoin(coin: string): { network: string; token?: string } {
  const parts = coin.split(":");
  if (parts.length === 2) {
    // Token format: "erc20:usdt"
    return {
      network: COIN_TO_NETWORK[parts[0]] || "ETH",
      token: parts[1].toUpperCase(),
    };
  }
  return {
    network: COIN_TO_NETWORK[coin] || coin.toUpperCase(),
  };
}

/**
 * Look up asset_network_id from database
 */
async function lookupAssetNetwork(
  client: PoolClient,
  assetSymbol: string,
  networkCode: string,
  contractAddress?: string
): Promise<{ id: string; decimals: number } | null> {
  const query = `
    SELECT an.id, a.decimals
    FROM asset_networks an
    JOIN assets a ON a.id = an.asset_id
    JOIN networks n ON n.id = an.network_id
    WHERE UPPER(a.symbol) = UPPER($1)
      AND UPPER(n.code) = UPPER($2)
      AND (
        LOWER(an.contract_address) = LOWER($3)
        OR (an.contract_address IS NULL AND $3 IS NULL)
      )
      AND an.deposits_enabled = true
  `;

  const result = await client.query(query, [
    assetSymbol,
    networkCode,
    contractAddress || null,
  ]);

  if (result.rows.length === 0) {
    return null;
  }

  return {
    id: result.rows[0].id,
    decimals: result.rows[0].decimals,
  };
}

/**
 * Normalize BitGo webhook to DepositObservation
 */
export async function normalizeBitGoWebhook(
  payload: BitGoWebhookPayload,
  client: PoolClient,
  logger: Logger
): Promise<DepositObservation[]> {
  const observations: DepositObservation[] = [];

  // Only process transfer webhooks
  if (payload.type !== "transfer" || !payload.transfer) {
    logger.debug({ type: payload.type }, "Skipping non-transfer webhook");
    return observations;
  }

  const transfer = payload.transfer;
  const coinInfo = parseBitGoCoin(payload.coin);
  const networkCode = coinInfo.network;
  const assetSymbol = coinInfo.token || COIN_TO_ASSET[payload.coin] || payload.coin.toUpperCase();

  // Determine if this is incoming (deposit)
  // BitGo marks incoming transfers with positive value in entries/outputs
  const entries = transfer.entries || transfer.outputs || [];
  
  for (const [index, entry] of entries.entries()) {
    const value = typeof entry.value === "string"
      ? entry.value
      : entry.valueString || entry.value?.toString();

    if (!value) {
      logger.debug({ transferId: transfer.id, index }, "Skipping BitGo entry without a value");
      continue;
    }
    
    const numericValue = BigInt(value);
    
    // Skip outgoing transfers (negative or zero value)
    if (numericValue <= 0n) {
      continue;
    }

    // Look up asset network
    const assetNetwork = await lookupAssetNetwork(
      client,
      assetSymbol,
      networkCode,
      payload.tokenContractAddress
    );

    if (!assetNetwork) {
      logger.warn(
        {
          assetSymbol,
          networkCode,
          contractAddress: payload.tokenContractAddress,
        },
        "Asset network not found for deposit"
      );
      continue;
    }

    // Determine status
    let status: "DETECTED" | "CONFIRMED" | "FAILED" = "DETECTED";
    if (transfer.state === "confirmed") {
      status = "CONFIRMED";
    } else if (transfer.state === "failed" || transfer.state === "removed") {
      status = "FAILED";
    }

    // Create observation
    const observation: DepositObservation = {
      provider: "BITGO",
      providerEventId: `${transfer.id}:${index}:${entry.address}`,
      walletId: payload.walletId,
      coin: payload.coin,
      networkCode,
      assetSymbol,
      txid: transfer.txid,
      voutOrLogIndex: String(index),
      address: entry.address,
      tag: undefined, // TODO: extract memo/tag for MEMO_BASED networks
      amountAtoms: numericValue,
      confirmations: transfer.confirmations || 0,
      blockHeight: transfer.height,
      blockHash: transfer.heightId,
      observedAt: transfer.date ? new Date(transfer.date) : new Date(),
      status,
      transferId: transfer.id,
      raw: payload,
    };

    observations.push(observation);
  }

  return observations;
}
