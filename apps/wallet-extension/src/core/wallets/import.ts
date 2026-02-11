// wallets.json import/export

import type { WalletsJson, WalletEntry, Account } from '../../types/wallet';
import { addressFromPubkey } from '../crypto/address';
import { hexToBytes, bytesToHex } from '../crypto/pq';

export function parseWalletsJson(json: string): Account[] {
  const data: WalletsJson = JSON.parse(json);
  
  if (data.version !== 1) {
    throw new Error(`Unsupported wallets.json version: ${data.version}`);
  }
  
  const accounts: Account[] = [];
  
  for (const wallet of data.wallets) {
    const publicKey = hexToBytes(wallet.public_key_hex);
    const secretKey = wallet.secret_key_hex ? hexToBytes(wallet.secret_key_hex) : undefined;
    
    // Verify address matches public key
    const expectedAddress = addressFromPubkey(publicKey, wallet.alg_id);
    if (expectedAddress !== wallet.address) {
      console.warn(`Address mismatch for ${wallet.label}: expected ${expectedAddress}, got ${wallet.address}`);
    }
    
    accounts.push({
      label: wallet.label,
      address: wallet.address,
      algId: wallet.alg_id,
      algName: wallet.alg_name,
      publicKey,
      secretKey,
      createdAt: wallet.created_at,
      watchOnly: !secretKey,
    });
  }
  
  return accounts;
}

export function exportWalletsJson(
  accounts: Account[],
  includeSecrets: boolean = false
): string {
  const wallets: WalletEntry[] = accounts.map(acc => ({
    label: acc.label,
    address: acc.address,
    alg_id: acc.algId,
    alg_name: acc.algName,
    public_key_hex: bytesToHex(acc.publicKey),
    secret_key_hex: includeSecrets && acc.secretKey ? bytesToHex(acc.secretKey) : '0x',
    created_at: acc.createdAt,
  }));
  
  const data: WalletsJson = {
    version: 1,
    wallets,
  };
  
  return JSON.stringify(data, null, 2);
}

// Deduplicate accounts by address
export function deduplicateAccounts(accounts: Account[]): Account[] {
  const seen = new Set<string>();
  const unique: Account[] = [];
  
  for (const acc of accounts) {
    if (!seen.has(acc.address)) {
      seen.add(acc.address);
      unique.push(acc);
    }
  }
  
  return unique;
}

// Merge imported accounts with existing, preferring secrets from import
export function mergeAccounts(existing: Account[], imported: Account[]): Account[] {
  const merged = new Map<string, Account>();
  
  // Add existing accounts
  for (const acc of existing) {
    merged.set(acc.address, acc);
  }
  
  // Merge imported accounts (prefer if has secret)
  for (const acc of imported) {
    const existingAcc = merged.get(acc.address);
    
    if (!existingAcc || (acc.secretKey && !existingAcc.secretKey)) {
      merged.set(acc.address, acc);
    }
  }
  
  return Array.from(merged.values());
}
