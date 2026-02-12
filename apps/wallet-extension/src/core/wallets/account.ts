// Account management

import type { Account } from '../../types/wallet';
import { generateKeyPair, DILITHIUM3_ALG_ID } from '../crypto/pq';
import { addressFromPubkey } from '../crypto/address';
import { hexToBytes } from '../crypto/convert';

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
  const secretKey = hexToBytes(secretKeyHex, 'secretKeyHex');
  const publicKey = hexToBytes(publicKeyHex, 'publicKeyHex');
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
