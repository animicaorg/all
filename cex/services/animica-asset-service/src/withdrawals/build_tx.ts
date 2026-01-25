/**
 * Build Animica Transaction
 * 
 * Creates signed transaction for account-based model
 */

import type { Logger } from "pino";
import type { AnimicaRpcClient } from "../rpc/client.js";
import { createHash } from "crypto";

export interface BuildTxParams {
  from: string;
  to: string;
  value: string; // in atoms
  nonce: number;
  gas_limit: number;
  gas_price: string; // in atoms
  privateKey?: string; // optional, if using external signing
}

export interface BuiltTransaction {
  raw_tx: string; // hex-encoded signed transaction
  txid: string; // computed transaction ID
  nonce: number;
}

/**
 * Build and sign a transaction
 * 
 * Note: This is a simplified implementation. In production, you would:
 * 1. Use proper key management (HSM, KMS, etc.)
 * 2. Implement the actual Animica transaction format
 * 3. Use proper signing with Dilithium3 or other PQ crypto
 */
export async function buildTransaction(
  params: BuildTxParams,
  rpcClient: AnimicaRpcClient,
  logger: Logger
): Promise<BuiltTransaction> {
  logger.debug(
    {
      from: params.from,
      to: params.to,
      value: params.value,
      nonce: params.nonce,
      gas_limit: params.gas_limit,
      gas_price: params.gas_price,
    },
    "Building transaction"
  );
  
  // For Animica, we'll use wallet.send if available, or build raw tx
  // Check if we can use wallet.send (node wallet manages keys)
  try {
    const txid = await rpcClient.walletSend(
      params.to,
      params.value,
      (BigInt(params.gas_price) * BigInt(params.gas_limit)).toString()
    );
    
    logger.info({ txid, to: params.to, value: params.value }, "Transaction built via wallet.send");
    
    // Return a pseudo-raw tx (we don't have the actual bytes)
    return {
      raw_tx: txid, // Use txid as placeholder since we don't have raw bytes
      txid,
      nonce: params.nonce,
    };
  } catch (error) {
    logger.warn({ error }, "wallet.send not available, transaction must be signed externally");
    throw new Error("External signing not yet implemented. Configure hot wallet on node.");
  }
}

/**
 * Get next nonce for an address
 */
export async function getNextNonce(
  address: string,
  rpcClient: AnimicaRpcClient,
  logger: Logger
): Promise<number> {
  try {
    logger.debug({ address }, "Getting next nonce");
    
    // Try to get pending nonce from node
    // This is a placeholder - actual implementation depends on RPC methods available
    try {
      const result = await rpcClient.call<{ nonce: number }>("account.getNonce", [address]);
      return result.nonce;
    } catch (rpcError: any) {
      // If method not found, fall back to 0
      if (rpcError.message?.includes("not found") || rpcError.code === -32601) {
        logger.warn({ address }, "account.getNonce not available, using nonce 0");
        return 0;
      }
      throw rpcError;
    }
  } catch (error: any) {
    logger.error({ error, address }, "Failed to get nonce, using 0 as fallback");
    return 0;
  }
}

/**
 * Compute transaction ID from raw transaction
 */
export function computeTxId(rawTx: string): string {
  const hash = createHash("sha256").update(Buffer.from(rawTx, "hex")).digest();
  return hash.toString("hex");
}
