// Transaction builder for v2 transactions

import type { UnsignedTxV2, TxKind, TxTransfer, SignedTx, PqSignature } from '../../types/tx';
import { addressToBytes } from '../crypto/address';
import { sign } from '../crypto/pq';
import { getSigningBytes, encodeCanonical, getTxHash, getUnsignedHash } from './cbor';
import { bytesToHex } from '../crypto/pq';

export interface TxParams {
  chainId: number;
  from: string;
  to: string;
  amount: number;
  gasPrice: number;
  gasLimit: number;
  validAfter: number;
  validUntil: number;
  data?: Uint8Array;
}

export async function buildAndSignTransfer(
  params: TxParams,
  secretKey: Uint8Array,
  publicKey: Uint8Array,
  algId: number
): Promise<{ signedTx: SignedTx; txid: string; unsignedHash: string }> {
  // Generate salt for replay protection
  const salt = crypto.getRandomValues(new Uint8Array(32));
  
  // Build unsigned transaction
  const payload: TxTransfer = {
    to: addressToBytes(params.to),
    amount: params.amount,
  };
  
  if (params.data && params.data.length > 0) {
    payload.data = params.data;
  }
  
  const unsignedTx: UnsignedTxV2 = {
    v: 2,
    chainId: params.chainId,
    from: addressToBytes(params.from),
    gas: {
      price: params.gasPrice,
      limit: params.gasLimit,
    },
    payload: {
      t: 0, // TRANSFER
      v: payload,
    },
    validAfter: params.validAfter,
    validUntil: params.validUntil,
    salt,
  };
  
  // Sign transaction
  const signingBytes = getSigningBytes(unsignedTx);
  const signature = await sign(signingBytes, secretKey, algId);
  
  const pqSig: PqSignature = {
    alg: algId,
    pubkey: publicKey,
    sig: signature,
  };
  
  const signedTx: SignedTx = {
    tx: unsignedTx,
    sigs: [pqSig],
  };
  
  const txid = getTxHash(signedTx);
  const unsignedHash = getUnsignedHash(unsignedTx);
  
  return { signedTx, txid, unsignedHash };
}

export function encodeTxForRpc(signedTx: SignedTx): string {
  const encoded = encodeCanonical(signedTx);
  return '0x' + bytesToHex(encoded);
}
