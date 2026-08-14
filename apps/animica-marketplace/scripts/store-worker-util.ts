import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { prisma } from '../lib/db';
import { decodeAnimicaAddress } from '../lib/address';

// Shared plumbing for the store background workers (payment watcher, license anchorer,
// subscription renewer, payout worker, deposit watcher). Workers are oneshot processes run
// by systemd timers (deploy/systemd/*) whose ExecStart wraps them in `flock -n`; the
// Postgres advisory lock below is the second belt so a manual `npm run worker:*` can never
// race a timer run either. Logging is structured JSON lines (one object per line) so
// journalctl output stays grep-able and machine-parseable.

// ── structured logging ───────────────────────────────────────────────────────

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'critical';

export function makeLogger(worker: string) {
  return (level: LogLevel, event: string, fields: Record<string, unknown> = {}) => {
    const line = JSON.stringify({
      ts: new Date().toISOString(),
      worker,
      level,
      event,
      ...jsonSafeFields(fields),
    });
    if (level === 'error' || level === 'critical') console.error(line);
    else console.log(line);
  };
}

function jsonSafeFields(fields: Record<string, unknown>): Record<string, unknown> {
  return JSON.parse(JSON.stringify(fields, (_k, v) => (typeof v === 'bigint' ? v.toString() : v)));
}

// ── single-instance lock (Postgres advisory; released on disconnect/crash) ──

export async function acquireAdvisoryLock(name: string): Promise<boolean> {
  // Stable 64-bit key from the worker name (same trick as pg's hashtext, done client-side
  // so the key is deterministic across pg versions).
  const digest = createHash('sha3-256').update(`animica-store-worker:${name}`).digest();
  const key = digest.readBigInt64BE(0);
  const rows = await prisma.$queryRaw<{ locked: boolean }[]>`SELECT pg_try_advisory_lock(${key}) AS locked`;
  return rows.length > 0 && rows[0].locked === true;
}

// ── CLI flags ────────────────────────────────────────────────────────────────

export interface WorkerFlags {
  dryRun: boolean;
}

// --dry-run (or DRY_RUN=1) => full observe/verify pass, ZERO writes and ZERO sends.
export function parseFlags(argv = process.argv.slice(2)): WorkerFlags {
  return { dryRun: argv.includes('--dry-run') || process.env.DRY_RUN === '1' };
}

// ── durable worker state (var/store-workers/<name>.json, atomic replace) ─────

const STATE_DIR = process.env.STORE_WORKER_STATE_DIR || join(process.cwd(), 'var', 'store-workers');

export function statePath(name: string): string {
  return join(STATE_DIR, `${name}.json`);
}

export function loadState<T>(name: string, fallback: T): T {
  try {
    return { ...fallback, ...JSON.parse(readFileSync(statePath(name), 'utf8')) };
  } catch {
    return fallback;
  }
}

export function saveState(name: string, state: unknown): void {
  const path = statePath(name);
  mkdirSync(dirname(path), { recursive: true });
  const tmp = `${path}.tmp`;
  writeFileSync(tmp, JSON.stringify(state, (_k, v) => (typeof v === 'bigint' ? v.toString() : v), 2));
  renameSync(tmp, path);
}

// ── chain value/address coercion (fail-closed) ───────────────────────────────

// Parse an RPC-provided amount into an exact bigint. Numbers above 2^53 have already lost
// precision in JSON.parse, so they are REJECTED (null), never guessed at.
export function toNanm(v: unknown): bigint | null {
  try {
    if (typeof v === 'bigint') return v;
    if (typeof v === 'number') return Number.isSafeInteger(v) ? BigInt(v) : null;
    if (typeof v === 'string') {
      const s = v.trim().toLowerCase();
      if (/^0x[0-9a-f]+$/.test(s)) return BigInt(s);
      if (/^[0-9]+$/.test(s)) return BigInt(s);
    }
    return null;
  } catch {
    return null;
  }
}

// The node may render an address as bech32m (anim1...) OR as 0x-hex of the 34-byte payload
// (algId u16 BE || sha3_256(pubkey)). Compare across both forms; ANY parse failure => false.
export function sameAddress(a: unknown, b: unknown): boolean {
  const na = normalizeAddress(a);
  const nb = normalizeAddress(b);
  return na !== null && nb !== null && na === nb;
}

function normalizeAddress(v: unknown): string | null {
  // Canonical form = the 32-byte account digest. The node's RPC renders
  // addresses as 0x + digest hex (64 chars, NO algId prefix — verified against
  // a live tx: bech32m payload minus its 2-byte algId equals tx.to exactly),
  // so every representation must collapse to the digest before comparing.
  if (typeof v !== 'string' || !v.trim()) return null;
  const s = v.trim().toLowerCase();
  if (s.startsWith('anim1')) {
    try {
      const d = decodeAnimicaAddress(s);
      return Buffer.from(d.digest).toString('hex');
    } catch {
      return null;
    }
  }
  const h = s.startsWith('0x') ? s.slice(2) : s;
  if (/^[0-9a-f]{68}$/.test(h)) return h.slice(4); // 34-byte payload → drop algId
  if (/^[0-9a-f]{64}$/.test(h)) return h; // node form: digest only
  return null;
}

export function normalizeHex(v: unknown): string | null {
  if (typeof v !== 'string') return null;
  const s = v.trim().toLowerCase();
  const h = s.startsWith('0x') ? s.slice(2) : s;
  return /^[0-9a-f]*$/.test(h) && h.length % 2 === 0 ? h : null;
}

export function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
