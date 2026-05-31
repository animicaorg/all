// Account management

import type { Account } from '../../types/wallet';
import { generateKeyPair, DILITHIUM3_ALG_ID, SPHINCSPLUS_ALG_ID, ML_DSA_65_ALG_ID } from '../crypto/pq';
import { addressFromPubkey } from '../crypto/address';
import { hexToBytes } from '../crypto/convert';

function algNameFor(algId: number): string {
  if (algId === DILITHIUM3_ALG_ID) return 'dilithium3';
  if (algId === SPHINCSPLUS_ALG_ID) return 'sphincs_shake_128s';
  if (algId === ML_DSA_65_ALG_ID) return 'ml_dsa_65';
  return 'unknown';
}

export function createAccount(label: string): Account {
  // generateKeyPair() returns a real FIPS 204 ML-DSA-65 keypair via
  // @noble/post-quantum (chain v2 default, alg_id 0x1003). The chain's
  // pq.py.algs.ml_dsa_65 verifier accepts these signatures byte-for-byte.
  const { publicKey, secretKey, algId } = generateKeyPair();
  const address = addressFromPubkey(publicKey, algId);

  return {
    label,
    address,
    algId,
    algName: algNameFor(algId),
    publicKey,
    secretKey,
    createdAt: new Date().toISOString(),
  };
}

export function importFromPrivateKey(
  label: string,
  secretKeyHex: string,
  publicKeyHex: string,
  algId: number = ML_DSA_65_ALG_ID,
): Account {
  const secretKey = hexToBytes(secretKeyHex, 'secretKeyHex');
  const publicKey = hexToBytes(publicKeyHex, 'publicKeyHex');
  const address = addressFromPubkey(publicKey, algId);

  return {
    label,
    address,
    algId,
    algName: algNameFor(algId),
    publicKey,
    secretKey,
    createdAt: new Date().toISOString(),
  };
}

export function createWatchOnlyAccount(label: string, address: string): Account {
  return {
    label,
    address,
    algId: 0,
    algName: 'watch-only',
    publicKey: new Uint8Array(0),
    createdAt: new Date().toISOString(),
    watchOnly: true,
  };
}
