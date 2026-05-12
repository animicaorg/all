/**
 * RPC helpers for transaction submission
 */

import type { ChainContext } from './types';
import { hexToBytes } from './signing';

/**
 * Fetch chain identity from RPC node
 * 
 * This is REQUIRED before signing any transaction.
 */
export async function fetchChainContext(
  rpcCall: (method: string, params: any) => Promise<any>
): Promise<ChainContext> {
  try {
    const attempts = ['chain.getChainIdentity', 'chain_getChainIdentity', 'chain.getIdentity'];
    let identity: any = null;
    let lastError: unknown = null;

    for (const method of attempts) {
      try {
        identity = await rpcCall(method, []);
        if (identity) break;
      } catch (error) {
        lastError = error;
      }
    }
    
    if (!identity) {
      throw new Error(`chain identity returned empty result${lastError ? ` (${String((lastError as any)?.message || lastError)})` : ''}`);
    }
    
    // Extract fields
    const chainIdRaw = identity.chainId ?? identity.chain_id;
    const genesisHashHex = identity.genesisHash || identity.genesis_hash;
    const network = identity.network || identity.name || 'unknown';
    const forkId = identity.forkId || identity.fork_id || null;

    let chainId = Number.NaN;
    if (typeof chainIdRaw === 'number') {
      chainId = chainIdRaw;
    } else if (typeof chainIdRaw === 'string') {
      try {
        chainId = chainIdRaw.startsWith('0x') ? Number(BigInt(chainIdRaw)) : Number(chainIdRaw);
      } catch {
        chainId = Number.NaN;
      }
    }
    if (!Number.isFinite(chainId) || !Number.isInteger(chainId) || chainId < 0) {
      throw new Error(`Invalid chain_id: ${chainIdRaw}`);
    }
    
    if (typeof genesisHashHex !== 'string' || !genesisHashHex) {
      throw new Error(`Invalid genesis_hash: ${genesisHashHex}`);
    }
    
    const genesisHash = hexToBytes(genesisHashHex);
    if (genesisHash.length !== 32) {
      throw new Error(`genesis_hash must be 32 bytes, got ${genesisHash.length}`);
    }
    
    return {
      chain_id: chainId,
      genesis_hash: genesisHash,
      network: String(network),
      fork_id: forkId !== null ? Number(forkId) : null,
      domain: 'tx',
      prehash: 'sha3-512',
    };
  } catch (error: any) {
    throw new Error(`Failed to fetch chain context: ${error.message || error}`);
  }
}

/**
 * Submit a raw transaction to the RPC node
 * 
 * @param rawTx - 0x-prefixed hex encoded transaction envelope
 * @param rpcCall - RPC call function
 * @returns Transaction hash
 * 
 * NOTE: We always submit positional params ([rawTx]) for maximum node compatibility.
 */
export async function submitTransaction(
  rawTx: string,
  rpcCall: (method: string, params: any) => Promise<any>
): Promise<string> {
  if (!rawTx.startsWith('0x')) {
    throw new Error('rawTx must start with 0x');
  }
  if (!/^0x[0-9a-f]+$/i.test(rawTx)) {
    throw new Error('rawTx must be 0x-prefixed hex');
  }

  try {
    const result = await rpcCall('tx.sendRawTransaction', [rawTx] as any);
    return result;
  } catch (error: any) {
    // Enhanced error handling for signature verification failures
    const message = error.message || String(error);
    
    if (message.includes('Invalid post-quantum signature')) {
      throw new Error(
        'Transaction signature verification failed on node. ' +
        'This indicates a mismatch in signing process. ' +
        'Original error: ' + message
      );
    }
    
    if (message.includes('verification failed')) {
      throw new Error(
        'PQ signature verification failed. ' +
        'Ensure you are signing with the correct key and algorithm. ' +
        'Original error: ' + message
      );
    }
    
    throw error;
  }
}

/**
 * Parse RPC error to extract useful information
 */
export function parseRpcError(error: any): {
  code?: number;
  message: string;
  data?: any;
} {
  if (error && typeof error === 'object') {
    return {
      code: error.code,
      message: error.message || String(error),
      data: error.data,
    };
  }
  
  return {
    message: String(error),
  };
}

/**
 * Check if an error is a signature verification error
 */
export function isSignatureError(error: any): boolean {
  const message = String(error.message || error).toLowerCase();
  return (
    message.includes('signature') ||
    message.includes('pq_verify') ||
    message.includes('-32012') ||
    message.includes('verification failed')
  );
}
