import { createHash } from 'node:crypto';
import { prisma } from './db';

// Content-addressed storage for the .anm gateway. A CID is a stable, verifiable name for bytes:
// anm1c<sha3_256(bytes) hex>. The gateway serves objects by CID; a domain's contentCid points at one.
// This is the storage/CID primitive nodes replicate for hosting rewards.

export const CID_PREFIX = 'anm1c';
export const MAX_CONTENT_BYTES = 2 * 1024 * 1024; // 2 MB per object (self-contained sites)

export function computeCid(bytes: Buffer): string {
  return CID_PREFIX + createHash('sha3-256').update(bytes).digest('hex');
}

export function isCid(s: string | null | undefined): boolean {
  return !!s && /^anm1c[0-9a-f]{64}$/.test(s);
}

// Store bytes; returns the CID. Idempotent — identical bytes collapse to the same object.
export async function putObject(
  bytes: Buffer,
  mime: string,
  publishedBy?: string,
): Promise<{ cid: string; size: number; deduped: boolean }> {
  if (bytes.length === 0) throw new Error('empty content');
  if (bytes.length > MAX_CONTENT_BYTES) throw new Error(`content too large (max ${MAX_CONTENT_BYTES} bytes)`);
  const cid = computeCid(bytes);
  const existing = await prisma.contentObject.findUnique({ where: { cid }, select: { cid: true } });
  if (existing) return { cid, size: bytes.length, deduped: true };
  await prisma.contentObject.create({
    data: { cid, mime, bytes, size: bytes.length, publishedBy: publishedBy ?? null },
  });
  return { cid, size: bytes.length, deduped: false };
}

export async function getObject(cid: string): Promise<{ mime: string; bytes: Buffer } | null> {
  if (!isCid(cid)) return null;
  const o = await prisma.contentObject.findUnique({ where: { cid }, select: { mime: true, bytes: true } });
  if (!o) return null;
  return { mime: o.mime, bytes: Buffer.from(o.bytes) };
}

// Cheap metadata read (mime + size) WITHOUT loading the bytes — for listing/serve decisions.
export async function statObject(cid: string): Promise<{ mime: string; size: number } | null> {
  if (!isCid(cid)) return null;
  const o = await prisma.contentObject.findUnique({ where: { cid }, select: { mime: true, size: true } });
  return o ? { mime: o.mime, size: o.size } : null;
}

// Verify stored bytes still hash to their CID (tamper check the gateway can run before serving).
export function verifyObject(cid: string, bytes: Buffer): boolean {
  return isCid(cid) && computeCid(bytes) === cid;
}
