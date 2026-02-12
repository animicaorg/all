export interface NormalizedWalletRecord {
  label: string;
  address: string;
  algId: number;
  algName: string;
  publicKeyHex: string;
  secretKeyHex?: string;
  createdAt: string;
}

export interface WalletImportParseResult {
  records: Array<{ index: number; record: NormalizedWalletRecord }>;
  errors: Array<{ index: number; reason: string }>;
}

function asHex(value: unknown, field: string): string {
  if (typeof value !== 'string') {
    throw new Error(`${field} must be a string`);
  }

  const raw = value.startsWith('0x') ? value.slice(2) : value;
  if (!raw || raw.length % 2 !== 0 || !/^[0-9a-fA-F]+$/.test(raw)) {
    throw new Error(`${field} must be valid even-length hex`);
  }

  return `0x${raw.toLowerCase()}`;
}

function parseWalletRecord(record: any, idx: number): NormalizedWalletRecord {
  if (!record || typeof record !== 'object') {
    throw new Error(`wallet[${idx}] must be an object`);
  }

  const address = String(record.address || '').trim();
  if (!address) {
    throw new Error(`wallet[${idx}] missing address`);
  }

  const algId = Number(record.alg_id ?? record.algId);
  if (!Number.isInteger(algId)) {
    throw new Error(`wallet[${idx}] invalid alg_id`);
  }

  const label = String(record.label || `Imported wallet ${idx + 1}`);
  const algName = String(record.alg_name ?? record.algName ?? `alg-${algId}`);
  const publicKeyHex = asHex(record.public_key_hex ?? record.publicKeyHex, `wallet[${idx}].public_key_hex`);

  let secretKeyHex: string | undefined;
  const secretCandidate = record.secret_key_hex ?? record.secretKeyHex;
  if (secretCandidate !== undefined && secretCandidate !== null && secretCandidate !== '') {
    secretKeyHex = asHex(secretCandidate, `wallet[${idx}].secret_key_hex`);
  }

  const createdAt = typeof record.created_at === 'string' && record.created_at
    ? record.created_at
    : (typeof record.createdAt === 'string' && record.createdAt ? record.createdAt : new Date().toISOString());

  return {
    label,
    address,
    algId,
    algName,
    publicKeyHex,
    secretKeyHex,
    createdAt,
  };
}

export function parseWalletImport(inputText: string): WalletImportParseResult {
  const parsed = JSON.parse(inputText);

  let rawRecords: any[];
  if (Array.isArray(parsed)) {
    rawRecords = parsed;
  } else if (parsed && typeof parsed === 'object' && Array.isArray((parsed as any).wallets)) {
    rawRecords = (parsed as any).wallets;
  } else if (parsed && typeof parsed === 'object') {
    rawRecords = [parsed];
  } else {
    throw new Error('Unsupported wallets import file shape');
  }

  const result: WalletImportParseResult = { records: [], errors: [] };

  rawRecords.forEach((record, idx) => {
    try {
      result.records.push({ index: idx, record: parseWalletRecord(record, idx) });
    } catch (error: any) {
      result.errors.push({ index: idx, reason: error?.message || 'Invalid wallet record' });
    }
  });

  return result;
}
