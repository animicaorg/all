// wallets.json import/export (canonical v2 + legacy support)

import type { WalletsJson, WalletEntry, Account } from '../../types/wallet';
import type { NetworkConfig } from '../../types/network';
import { addressFromPubkey, decodeAddress } from '../crypto/address';
import { hexToBytes, bytesToHex } from '../crypto/pq';

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
  };

  const secretRaw = wallet.secret_key_hex ?? wallet.secretKeyHex;
  if (typeof secretRaw === 'string' && secretRaw !== '0x' && secretRaw.trim()) {
    out.secret_key_hex = normalizeHex(secretRaw, `wallet[${idx}].secret_key_hex`);
  }

  if (typeof wallet.private_key_enc === 'string') out.private_key_enc = wallet.private_key_enc;
  if (wallet.keystore && typeof wallet.keystore === 'object') out.keystore = wallet.keystore;
  if (wallet.meta && typeof wallet.meta === 'object') out.meta = wallet.meta;

  return out;
}

interface ParseWalletsJsonOptions {
  network?: NetworkConfig;
}

function assertWalletMatchesNetwork(idx: number, wallet: WalletEntry, network?: NetworkConfig) {
  let decoded;
  try {
    decoded = decodeAddress(wallet.address, network
      ? { expectedHrp: network.addressHrp, supportedVersions: network.supportedAddressVersions }
      : undefined
    );
  } catch (error) {
    if (network && error instanceof Error && error.message.startsWith('Unsupported address version')) {
      throw new Error(`wallet[${idx}] ${error.message}. Switch network and retry import.`);
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

  if (!network.supportedAddressVersions.includes(decoded.version)) {
    throw new Error(
      `wallet[${idx}] uses address version ${decoded.version}, but network ${network.name} supports: ${network.supportedAddressVersions.join(',')}. Switch network and retry import.`
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
    walletsRaw = Object.entries(data)
      .filter(([, v]) => typeof v === 'object' && v !== null)
      .map(([k, v]) => ({ ...(v as any), label: (v as any).label ?? k }));
  } else {
    throw new Error('Unsupported wallets.json shape');
  }

  const accounts: Account[] = [];

  walletsRaw.forEach((wallet, idx) => {
    const normalized = normalizeWalletEntry(wallet, idx);
    const decodedAddress = assertWalletMatchesNetwork(idx, normalized, options.network);

    const publicKey = hexToBytes(normalized.public_key_hex);
    const secretHex = normalized.secret_key_hex ?? normalized.private_key_enc;
    const secretKey = secretHex ? hexToBytes(secretHex) : undefined;

    const expectedAddress = addressFromPubkey(publicKey, normalized.alg_id, {
      expectedHrp: options.network?.addressHrp,
      supportedVersions: decodedAddress ? [decodedAddress.version] : options.network?.supportedAddressVersions,
    });
    if (expectedAddress !== normalized.address) {
      console.warn(`Address mismatch for ${normalized.label}: expected ${expectedAddress}, got ${normalized.address}`);
    }

    accounts.push({
      label: normalized.label,
      address: normalized.address,
      algId: normalized.alg_id,
      algName: normalized.alg_name ?? `alg-${normalized.alg_id}`,
      publicKey,
      secretKey,
      createdAt: normalized.created_at,
      watchOnly: !secretKey,
    });
  });

  return accounts;
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

  for (const acc of existing) {
    merged.set(acc.address, acc);
  }

  for (const acc of imported) {
    const existingAcc = merged.get(acc.address);
    if (!existingAcc || (acc.secretKey && !existingAcc.secretKey)) {
      merged.set(acc.address, acc);
    }
  }

  return Array.from(merged.values());
}
