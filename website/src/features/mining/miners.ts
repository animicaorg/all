import type { MiningMinerItem } from './types';

const HASHRATE_MULTIPLIER: Record<string, number> = {
  '': 1,
  k: 1_000,
  m: 1_000_000,
  g: 1_000_000_000,
  t: 1_000_000_000_000,
  p: 1_000_000_000_000_000,
};

export function extractMinerItems(payload: unknown): MiningMinerItem[] {
  if (Array.isArray(payload)) {
    return payload.filter((item): item is MiningMinerItem => isRecord(item));
  }

  if (!isRecord(payload)) return [];

  const items = Array.isArray(payload.items)
    ? payload.items
    : Array.isArray(payload.miners)
      ? payload.miners
      : [];

  return items.filter((item): item is MiningMinerItem => isRecord(item));
}

export function rankMiners(items: MiningMinerItem[]): MiningMinerItem[] {
  return [...items].sort((left, right) => {
    const hashrateDelta = resolveMinerHashrate(right) - resolveMinerHashrate(left);
    if (Math.abs(hashrateDelta) > Number.EPSILON) return hashrateDelta;

    const blocksDelta = readNonNegativeNumber(right.blocks_found) - readNonNegativeNumber(left.blocks_found);
    if (blocksDelta !== 0) return blocksDelta;

    const sharesDelta =
      readNonNegativeNumber(right.shares_accepted) - readNonNegativeNumber(left.shares_accepted);
    if (sharesDelta !== 0) return sharesDelta;

    return readWorkerLabel(left).localeCompare(readWorkerLabel(right));
  });
}

export function resolveMinerHashrate(item: MiningMinerItem): number {
  const candidates: unknown[] = [
    item.hashrate_1m,
    item.hashrate_15m,
    item.hashrate_1h,
    item.hashrate,
    item.hashrate_hps,
    item.hashrate_hsps,
  ];

  for (const candidate of candidates) {
    const parsed = parseHashrateValue(candidate);
    if (parsed !== undefined) return parsed;
  }

  return 0;
}

export function parseHashrateValue(value: unknown): number | undefined {
  if (typeof value === 'number') {
    return Number.isFinite(value) && value >= 0 ? value : undefined;
  }

  if (typeof value !== 'string') return undefined;
  const raw = value.trim();
  if (!raw) return undefined;

  const plain = raw.replace(/,/g, '');
  const direct = Number(plain);
  if (Number.isFinite(direct) && direct >= 0) return direct;

  const parsedWithUnit =
    parseHashrateWithUnit(plain) ??
    parseHashrateWithUnit(plain.toLowerCase());
  if (parsedWithUnit !== undefined) return parsedWithUnit;

  return undefined;
}

function parseHashrateWithUnit(value: string): number | undefined {
  const exact = value.match(
    /^([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*([kmgtp]?)\s*h(?:\/s|s|ps)?$/i
  );
  const match =
    exact ??
    value.match(/([+-]?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*([kmgtp]?)\s*h(?:\/s|s|ps)\b/i);
  if (!match) return undefined;

  const base = Number(match[1]);
  const unit = String(match[2] ?? '').toLowerCase();
  if (!Number.isFinite(base) || base < 0) return undefined;

  const multiplier = HASHRATE_MULTIPLIER[unit];
  if (!multiplier) return undefined;
  return base * multiplier;
}

function readWorkerLabel(item: MiningMinerItem): string {
  return String(item.worker_name ?? item.worker_id ?? '').toLowerCase();
}

function readNonNegativeNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value) && value >= 0) return value;
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value.replace(/,/g, ''));
    if (Number.isFinite(parsed) && parsed >= 0) return parsed;
  }
  return 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}
