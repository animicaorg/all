import type { NetworkConfig } from '../../types/network';
import type { Account } from '../../types/wallet';
import { addressFromPubkey } from '../../core/crypto/address';
import { hexToBytes } from '../../core/crypto/pq';
import { decodeAnimAddress } from '../address/animicaAddress';
import { parseWalletImport, type NormalizedWalletRecord } from './schema';

export interface ImportInvalidRecord {
  index: number;
  label?: string;
  reason: string;
}

export interface ImportResultSummary {
  imported_count: number;
  skipped_duplicates: number;
  upgraded_watch_only: number;
  invalid_records: ImportInvalidRecord[];
  total_accounts: number;
}

export interface WalletImportResult {
  accounts: Account[];
  summary: ImportResultSummary;
}

function dedupeKey(address: string, algId: number): string {
  return `${address}::${algId}`;
}

function versionCompatibilityMessage(reason: string): string {
  if (/excess padding/i.test(reason)) {
    return `Invalid bech32m address encoding (${reason}). Re-export the wallet file from the source wallet and retry import.`;
  }

  return reason;
}

function normalizeWalletRecord(record: NormalizedWalletRecord, network?: NetworkConfig): Account {
  let normalizedAddress = record.address;

  try {
    decodeAnimAddress(record.address, {
      expectedHrp: network?.addressHrp ?? 'anim',
    });
  } catch (error: any) {
    const reason = String(error?.message || '');
    if (!/excess padding/i.test(reason)) {
      throw error;
    }

    // Compatibility fallback for legacy/non-canonical wallets.json exports.
    // Derive a canonical bech32m address from the pubkey and continue import.
    const fallbackPublicKey = hexToBytes(record.publicKeyHex);
    normalizedAddress = addressFromPubkey(fallbackPublicKey, record.algId, {
      expectedHrp: network?.addressHrp ?? 'anim',
    });
  }

  const publicKey = hexToBytes(record.publicKeyHex);
  const secretKey = record.secretKeyHex ? hexToBytes(record.secretKeyHex) : undefined;

  const decodedAddress = decodeAnimAddress(normalizedAddress, {
    expectedHrp: network?.addressHrp ?? 'anim',
  });

  const expectedAddress = addressFromPubkey(publicKey, record.algId, {
    expectedHrp: decodedAddress.hrp,
  });

  if (expectedAddress !== normalizedAddress) {
    throw new Error('Address/public key mismatch');
  }

  return {
    label: record.label,
    address: normalizedAddress,
    algId: record.algId,
    algName: record.algName,
    publicKey,
    secretKey,
    createdAt: record.createdAt,
    watchOnly: !secretKey,
  };
}

export async function importWalletRecords(
  inputText: string,
  existingAccounts: Account[],
  network?: NetworkConfig,
): Promise<WalletImportResult> {
  const parsed = parseWalletImport(inputText);
  const result: ImportResultSummary = {
    imported_count: 0,
    skipped_duplicates: 0,
    upgraded_watch_only: 0,
    invalid_records: parsed.errors.map((error) => ({
      index: error.index,
      reason: versionCompatibilityMessage(error.reason),
    })),
    total_accounts: existingAccounts.length,
  };

  const merged = new Map<string, Account>();
  for (const account of existingAccounts) {
    merged.set(dedupeKey(account.address, account.algId), account);
  }

  for (const { index, record } of parsed.records) {
    await Promise.resolve();

    try {
      const importedAccount = normalizeWalletRecord(record, network);
      const key = dedupeKey(importedAccount.address, importedAccount.algId);
      const existing = merged.get(key);

      if (!existing) {
        merged.set(key, importedAccount);
        result.imported_count += 1;
        continue;
      }

      if ((existing.watchOnly || !existing.secretKey) && importedAccount.secretKey) {
        merged.set(key, {
          ...existing,
          ...importedAccount,
          watchOnly: false,
        });
        result.upgraded_watch_only += 1;
        continue;
      }

      result.skipped_duplicates += 1;
    } catch (error: any) {
      result.invalid_records.push({
        index,
        label: record.label,
        reason: versionCompatibilityMessage(error?.message || 'Unknown import error'),
      });
    }
  }

  const accounts = Array.from(merged.values());
  result.total_accounts = accounts.length;

  return {
    accounts,
    summary: result,
  };
}
