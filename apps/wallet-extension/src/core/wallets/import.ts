import type { WalletsJson, WalletEntry, Account } from '../../types/wallet';
import type { NetworkConfig } from '../../types/network';
import { bytesToHex, hexToBytes } from '../crypto/pq';
import { addressFromPubkey, decodeAddress } from '../crypto/address';
import { importWalletRecords } from '../../lib/walletImport/importer';

interface ParseWalletsJsonOptions {
  network?: NetworkConfig;
}

export interface WalletImportSummary {
  imported_count: number;
  skipped_duplicates: number;
  upgraded_watch_only: number;
  invalid_records: Array<{ index: number; label?: string; reason: string }>;
  total_accounts: number;
}

function normalizeHex(value: unknown, field: string): string {
  if (typeof value !== 'string') throw new Error(`${field} must be string`);
  const raw = value.startsWith('0x') ? value.slice(2) : value;
  if (!raw || raw.length % 2 !== 0 || !/^[0-9a-fA-F]+$/.test(raw)) {
    throw new Error(`${field} must be valid even-length hex`);
  }
  return `0x${raw.toLowerCase()}`;
}

function normalizeWalletEntry(wallet: any, idx: number): WalletEntry {
  const label = String(wallet.label ?? wallet.name ?? `wallet-${idx + 1}`);
  const address = String(wallet.address ?? wallet.addr ?? '');
  if (!address) throw new Error(`wallet[${idx}] missing address`);

  const algIdRaw = wallet.alg_id ?? wallet.algId ?? wallet.alg;
  const algId = Number(algIdRaw);
  if (!Number.isFinite(algId)) throw new Error(`wallet[${idx}] invalid alg_id`);

  const publicKeyHex = normalizeHex(
    wallet.public_key_hex ?? wallet.publicKeyHex ?? wallet.pubkey ?? wallet.pub ?? wallet.pk,
    `wallet[${idx}].public_key_hex`,
  );

  const createdAt = String(wallet.created_at ?? wallet.createdAt ?? new Date().toISOString());

  const out: WalletEntry = {
    label,
    address,
    alg_id: algId,
    alg_name: String(wallet.alg_name ?? wallet.algName ?? `alg-${algId}`),
    public_key_hex: publicKeyHex,
    created_at: createdAt,
    meta: wallet.meta,
  };

  const secretRaw = wallet.secret_key_hex ?? wallet.secretKeyHex;
  if (typeof secretRaw === 'string' && secretRaw !== '0x' && secretRaw.trim()) {
    out.secret_key_hex = normalizeHex(secretRaw, `wallet[${idx}].secret_key_hex`);
  }

  return out;
}

function assertWalletMatchesNetwork(idx: number, wallet: WalletEntry, network?: NetworkConfig) {
  let decoded;
  try {
    decoded = decodeAddress(wallet.address, network
      ? { expectedHrp: network.addressHrp }
      : undefined
    );
  } catch (error) {
    if (error instanceof Error && /excess padding/i.test(error.message)) {
      throw new Error(
        `wallet[${idx}] has invalid bech32m address encoding (${error.message}). Re-export the wallet file and retry import.`
      );
    }
    throw error;
  }

  if (!network) return decoded;

  const metaChainId = wallet.meta?.chain_id ?? wallet.meta?.chainId ?? wallet.meta?.network_id ?? wallet.meta?.networkId;
  if (metaChainId !== undefined && Number(metaChainId) !== network.chainId) {
    throw new Error(
      `wallet[${idx}] targets chain_id ${metaChainId}, but current network is ${network.chainId}. Switch network and retry import.`
    );
  }

  return decoded;
}

export function parseWalletsJson(json: string, options: ParseWalletsJsonOptions = {}): Account[] {
  const data: any = JSON.parse(json);

  let walletsRaw: any[];
  if (data?.format === 'animica.wallets' && Number(data?.version) === 2 && Array.isArray(data.wallets)) {
    walletsRaw = data.wallets;
  } else if (Array.isArray(data?.wallets)) {
    walletsRaw = data.wallets;
  } else if (Array.isArray(data)) {
    walletsRaw = data;
  } else if (data && typeof data === 'object') {
    walletsRaw = [data];
  } else {
    throw new Error('Unsupported wallets.json shape');
  }

  return walletsRaw.map((wallet, idx) => {
    const normalized = normalizeWalletEntry(wallet, idx);
    const decodedAddress = assertWalletMatchesNetwork(idx, normalized, options.network);

    const publicKey = hexToBytes(normalized.public_key_hex);
    const secretHex = normalized.secret_key_hex;
    const secretKey = secretHex ? hexToBytes(secretHex) : undefined;

    const expectedAddress = addressFromPubkey(publicKey, normalized.alg_id, {
      expectedHrp: options.network?.addressHrp,
    });
    if (expectedAddress !== normalized.address) {
      // Compatibility path for older exports that stored non-canonical addresses.
    }

    return {
      label: normalized.label,
      address: normalized.address,
      algId: normalized.alg_id,
      algName: normalized.alg_name ?? `alg-${normalized.alg_id}`,
      publicKey,
      secretKey,
      createdAt: normalized.created_at,
      watchOnly: !secretKey,
    };
  });
}

export async function importWalletsJson(
  json: string,
  existingAccounts: Account[],
  options: ParseWalletsJsonOptions = {},
): Promise<{ accounts: Account[]; summary: WalletImportSummary }> {
  return importWalletRecords(json, existingAccounts, options.network);
}

export function exportWalletsJson(
  accounts: Account[],
  includeSecrets: boolean = false,
): string {
  const now = new Date().toISOString();
  const wallets: WalletEntry[] = [...accounts]
    .sort((a, b) => a.label.localeCompare(b.label))
    .map((acc) => ({
      label: acc.label,
      address: acc.address,
      alg_id: acc.algId,
      alg_name: acc.algName,
      public_key_hex: bytesToHex(acc.publicKey),
      ...(includeSecrets && acc.secretKey ? { secret_key_hex: bytesToHex(acc.secretKey) } : {}),
      created_at: acc.createdAt,
    }));

  const data: WalletsJson = {
    format: 'animica.wallets',
    version: 2,
    created_at: now,
    updated_at: now,
    wallets,
  };

  return `${JSON.stringify(data, null, 2)}\n`;
}

export function deduplicateAccounts(accounts: Account[]): Account[] {
  const unique = new Map<string, Account>();
  for (const account of accounts) {
    unique.set(`${account.address}:${account.algId}`, account);
  }

  return Array.from(unique.values());
}

export function mergeAccounts(existing: Account[], imported: Account[]): Account[] {
  const merged = new Map<string, Account>();
  for (const account of existing) {
    merged.set(`${account.address}:${account.algId}`, account);
  }

  for (const account of imported) {
    const key = `${account.address}:${account.algId}`;
    const current = merged.get(key);

    if (!current) {
      merged.set(key, account);
      continue;
    }

    if ((current.watchOnly || !current.secretKey) && account.secretKey) {
      merged.set(key, { ...current, ...account, watchOnly: false });
    }
  }

  return Array.from(merged.values());
}
