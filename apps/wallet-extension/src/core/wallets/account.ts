// Account management

import type { Account } from '../../types/wallet';
import { generateKeyPair, DILITHIUM3_ALG_ID } from '../crypto/pq';
import { addressFromPubkey } from '../crypto/address';

export function createAccount(label: string): Account {
  const { publicKey, secretKey, algId } = generateKeyPair();
  const address = addressFromPubkey(publicKey, algId);
  
  return {
    label,
    address,
    algId,
    algName: 'dilithium3',
    publicKey,
    secretKey,
    createdAt: new Date().toISOString(),
  };
}

export function importFromPrivateKey(
  label: string,
  secretKeyHex: string,
  publicKeyHex: string,
  algId: number = DILITHIUM3_ALG_ID
): Account {
  const secretKey = hexToBytes(secretKeyHex);
  const publicKey = hexToBytes(publicKeyHex);
  const address = addressFromPubkey(publicKey, algId);
  
  return {
    label,
    address,
    algId,
    algName: algId === DILITHIUM3_ALG_ID ? 'dilithium3' : 'unknown',
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

function hexToBytes(hex: string): Uint8Array {
  const cleaned = hex.startsWith('0x') ? hex.slice(2) : hex;
  const bytes = new Uint8Array(cleaned.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(cleaned.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}
