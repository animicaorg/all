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
  // generateKeyPair() defaults to SPHINCS-SHAKE-128s (Animica's
  // pure-Python variant), which is what the chain's fallback verifier
  // accepts when ANIMICA_ALLOW_PQ_PURE_FALLBACK=1. Dilithium3 keygen
  // requires the real ML-DSA-65 reference impl that isn't ported to
  // TypeScript yet — accounts created here use SPHINCS so the resulting
  // signatures actually verify on chain.
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
  algId: number = SPHINCSPLUS_ALG_ID,
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
