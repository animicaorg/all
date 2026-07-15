import { createHash } from 'node:crypto';
import { NANM_PER_ANM } from './config';

// Animica Name Service helpers: name validation, registration pricing, and the Merkle root
// used to anchor the whole registry on-chain (tamper-evident via the live txsRoot).

const RESERVED = new Set(['anm', 'www', 'root', 'admin', 'animica', 'ans', 'registry', 'node', 'localhost']);

export function normalizeName(input: string): string {
  return (input || '').trim().toLowerCase().replace(/\.anm$/, '');
}

export function validateName(name: string): { ok: boolean; reason?: string } {
  const n = normalizeName(name);
  if (n.length < 2) return { ok: false, reason: 'name too short (min 2)' };
  if (n.length > 63) return { ok: false, reason: 'name too long (max 63)' };
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?$/.test(n)) return { ok: false, reason: 'use a-z, 0-9, hyphens (not at ends)' };
  if (n.includes('--')) return { ok: false, reason: 'no double hyphens' };
  if (RESERVED.has(n)) return { ok: false, reason: 'reserved name' };
  return { ok: true };
}

// Registration fee scales down with length (short names cost more — anti-squat + value signal).
export function registrationFeeNanm(name: string, years: number): bigint {
  const n = normalizeName(name);
  let perYear: bigint;
  if (n.length <= 3) perYear = 500n * NANM_PER_ANM;
  else if (n.length <= 5) perYear = 100n * NANM_PER_ANM;
  else if (n.length <= 8) perYear = 25n * NANM_PER_ANM;
  else perYear = 5n * NANM_PER_ANM;
  const y = BigInt(Math.max(1, Math.min(years, 10)));
  return perYear * y;
}

// Canonical hash of a single domain record (owner-signed content lives in the DB; this is what the
// anchor commits to). Deterministic ordering.
export function domainRecordHash(d: {
  name: string; owner: string; kind: string; contentCid: string | null;
  agentHandle: string | null; recordsJson: string; expiresAt: Date;
}): string {
  const canonical = JSON.stringify({
    name: d.name, owner: d.owner, kind: d.kind, contentCid: d.contentCid ?? '',
    agentHandle: d.agentHandle ?? '', records: d.recordsJson, expires: Math.floor(d.expiresAt.getTime() / 1000),
  });
  return createHash('sha3-256').update(canonical).digest('hex');
}

// Merkle root over sorted (name -> recordHash) leaves. Same shape the chain uses for its own roots.
// Operates on hex-string digests throughout (avoids Buffer subtype friction and stays deterministic).
export function merkleRoot(leaves: { name: string; hash: string }[]): string {
  if (!leaves.length) return createHash('sha3-256').update('animica/ans/empty').digest('hex');
  let level: string[] = leaves
    .slice()
    .sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
    .map((l) => createHash('sha3-256').update(`${l.name}:${l.hash}`).digest('hex'));
  while (level.length > 1) {
    const next: string[] = [];
    for (let i = 0; i < level.length; i += 2) {
      const a = level[i];
      const b = i + 1 < level.length ? level[i + 1] : level[i];
      next.push(createHash('sha3-256').update(a + b).digest('hex'));
    }
    level = next;
  }
  return level[0];
}
