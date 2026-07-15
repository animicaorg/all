import { authenticate, ok, err, ApiError } from '@/lib/api';
import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { domainRecordHash, merkleRoot } from '@/lib/ans';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/names/anchor -> the current Merkle root over all active .anm records.
// This root is what a signed on-chain data-tx commits to (tamper-evident via the live txsRoot),
// so anyone can audit the registry against the chain. Also returns the latest published anchor.
export async function GET() {
  try {
    const domains = await prisma.anmDomain.findMany({
      where: { status: 'ACTIVE' },
      include: { owner: { select: { address: true } } },
      orderBy: { name: 'asc' },
    });
    const leaves = domains.map((d) => ({
      name: d.name,
      hash: domainRecordHash({
        name: d.name, owner: d.owner.address, kind: d.kind,
        contentCid: d.contentCid, agentHandle: d.agentHandle, recordsJson: d.recordsJson, expiresAt: d.expiresAt,
      }),
    }));
    const root = merkleRoot(leaves);
    const latest = await prisma.domainAnchor.findFirst({ orderBy: { seq: 'desc' } });
    return ok({
      merkleRoot: root,
      domainCount: leaves.length,
      latestPublishedAnchor: latest ? jsonSafe(latest) : null,
      note: 'A signed data-tx publishes this root on-chain; the chain enforces txsRoot so an included anchor is tamper-evident (no consensus fork needed).',
    });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/names/anchor -> record a new pending anchor checkpoint (admin/worker).
// The actual on-chain data-tx is published by `animica names anchor` (programmatic tx builder).
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx || !ctx.scopes.includes('*')) throw new ApiError(403, 'forbidden', 'session (owner) auth required');
    const domains = await prisma.anmDomain.findMany({ where: { status: 'ACTIVE' }, include: { owner: { select: { address: true } } }, orderBy: { name: 'asc' } });
    const leaves = domains.map((d) => ({ name: d.name, hash: domainRecordHash({ name: d.name, owner: d.owner.address, kind: d.kind, contentCid: d.contentCid, agentHandle: d.agentHandle, recordsJson: d.recordsJson, expiresAt: d.expiresAt }) }));
    const root = merkleRoot(leaves);
    const prev = await prisma.domainAnchor.findFirst({ orderBy: { seq: 'desc' } });
    const anchor = await prisma.domainAnchor.create({
      data: { seq: (prev?.seq ?? 0) + 1, merkleRoot: root, domainCount: leaves.length, prevAnchor: prev?.txid ?? null, status: 'pending' },
    });
    return ok({ anchor: jsonSafe(anchor) }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
