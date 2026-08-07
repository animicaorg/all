import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/denylist — malicious-code hash blocking (§39).
//   GET  -> blocked sha3-256 fingerprints (+ which live versions match, so blocking is informed)
//   POST {sha3, action:'add'|'remove', reason}

const SHA3_RE = /^[0-9a-f]{64}$/;

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const { take, skip } = pageParams(req, 100);
    const [rows, total] = await Promise.all([
      prisma.cloudCodeDenylist.findMany({ orderBy: { createdAt: 'desc' }, take, skip }),
      prisma.cloudCodeDenylist.count(),
    ]);
    const hashes = rows.map((r) => r.sha3);
    const matches = hashes.length
      ? await prisma.cloudFunctionVersion.findMany({
          where: { OR: [{ sourceSha3: { in: hashes } }, { artifactSha3: { in: hashes } }] },
          select: {
            sourceSha3: true,
            artifactSha3: true,
            version: true,
            function: { select: { id: true, slug: true, status: true, owner: { select: { address: true, handle: true } } } },
          },
        })
      : [];
    return ok({ rows, total, matches });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const sha3 = requireString(body, 'sha3', 64).toLowerCase();
    const action = requireString(body, 'action', 20);
    const reason = optionalString(body, 'reason');
    if (!SHA3_RE.test(sha3)) throw new ApiError(400, 'bad_request', 'sha3 must be 64 hex chars (sha3-256)');

    if (action === 'add') {
      if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required to block a code hash");
      const existing = await prisma.cloudCodeDenylist.findUnique({ where: { sha3 } });
      const row = await prisma.$transaction(async (tx) => {
        const created = await tx.cloudCodeDenylist.upsert({
          where: { sha3 },
          create: { sha3, reason, addedBy: actor },
          update: { reason, addedBy: actor },
        });
        await audit(tx, actor, 'denylist.add', `code_hash:${sha3}`, existing ?? { blocked: false }, { blocked: true, reason }, reason);
        return created;
      });
      return ok({ row });
    }

    if (action === 'remove') {
      if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required to unblock a code hash");
      const existing = await prisma.cloudCodeDenylist.findUnique({ where: { sha3 } });
      if (!existing) throw new ApiError(404, 'not_found', 'hash is not on the denylist');
      await prisma.$transaction(async (tx) => {
        await tx.cloudCodeDenylist.delete({ where: { sha3 } });
        await audit(tx, actor, 'denylist.remove', `code_hash:${sha3}`, { blocked: true, reason: existing.reason }, { blocked: false }, reason);
      });
      return ok({ removed: sha3 });
    }

    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
