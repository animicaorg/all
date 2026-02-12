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
  if (reason.includes('Unsupported address version: 2') || reason.includes('Unsupported address version 2')) {
    return 'Your wallet file contains address version 2; this extension now supports v1/v2. If you still see this, update and retry.';
  }

  return reason;
}

function normalizeWalletRecord(record: NormalizedWalletRecord, network?: NetworkConfig): Account {
  const decodedAddress = decodeAnimAddress(record.address, {
    expectedHrp: network?.addressHrp ?? 'anim',
    supportedVersions: [1, 2],
  });

  if (network && !network.supportedAddressVersions.includes(decodedAddress.version)) {
    throw new Error(`Unsupported address version ${decodedAddress.version} (supported: ${network.supportedAddressVersions.join(',')})`);
  }

  const publicKey = hexToBytes(record.publicKeyHex);
  const secretKey = record.secretKeyHex ? hexToBytes(record.secretKeyHex) : undefined;

  const expectedAddress = addressFromPubkey(publicKey, record.algId, {
    expectedHrp: decodedAddress.hrp,
    supportedVersions: [decodedAddress.version],
  });

  if (expectedAddress !== record.address) {
    throw new Error('Address/public key mismatch');
  }

  return {
    label: record.label,
    address: record.address,
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
