import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/deployments — deployment lifecycle oversight (§39).
//   GET  ?status= (default FAILED) -> deployment attempts with function/owner/version context.
//   POST {deploymentId, action, reason}
//     action 'disable'    -> PAUSE a compromised ACTIVE deployment (endpoint stops serving)
//     action 'block_hash' -> add the version's source+artifact hashes to CloudCodeDenylist so
//                            the exact code can never deploy again (and disable this deployment)
// Both are availability actions: audited with before/after.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const status = url.searchParams.get('status') ?? 'FAILED';
    const { take, skip } = pageParams(req);
    const where = status === 'all' ? {} : { status: status as any };
    const [rows, total, failed7d] = await Promise.all([
      prisma.cloudDeployment.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        include: {
          function: { select: { id: true, slug: true, name: true, status: true, owner: { select: { address: true, handle: true } } } },
          version: { select: { version: true, sourceSha3: true, artifactSha3: true, sizeBytes: true } },
        },
      }),
      prisma.cloudDeployment.count({ where }),
      prisma.cloudDeployment.count({ where: { status: 'FAILED', createdAt: { gte: new Date(Date.now() - 7 * 86_400_000) } } }),
    ]);
    return ok({ rows, total, failed7d });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const deploymentId = requireString(body, 'deploymentId');
    const action = requireString(body, 'action', 40);
    const reason = optionalString(body, 'reason');
    if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required (audited availability action)");

    const dep = await prisma.cloudDeployment.findUnique({
      where: { id: deploymentId },
      include: { version: { select: { sourceSha3: true, artifactSha3: true } }, function: { select: { id: true, slug: true } } },
    });
    if (!dep) throw new ApiError(404, 'not_found', 'deployment not found');

    if (action === 'disable') {
      if (dep.status === 'PAUSED') throw new ApiError(409, 'conflict', 'deployment is already paused');
      const updated = await prisma.$transaction(async (tx) => {
        const row = await tx.cloudDeployment.update({
          where: { id: deploymentId },
          data: { status: 'PAUSED', error: `disabled by admin: ${reason}`.slice(0, 500) },
        });
        await audit(
          tx,
          actor,
          'deployment.disable',
          `cloud_deployment:${deploymentId}`,
          { status: dep.status, functionSlug: dep.function.slug },
          { status: 'PAUSED' },
          reason,
        );
        return row;
      });
      return ok({ deployment: updated });
    }

    if (action === 'block_hash') {
      const hashes = [dep.version.sourceSha3, dep.version.artifactSha3].filter(
        (h, i, arr) => h && arr.indexOf(h) === i,
      );
      const result = await prisma.$transaction(async (tx) => {
        for (const sha3 of hashes) {
          await tx.cloudCodeDenylist.upsert({
            where: { sha3 },
            create: { sha3, reason, addedBy: actor },
            update: { reason, addedBy: actor },
          });
        }
        const row =
          dep.status === 'PAUSED'
            ? dep
            : await tx.cloudDeployment.update({
                where: { id: deploymentId },
                data: { status: 'PAUSED', error: `code hash blocked by admin: ${reason}`.slice(0, 500) },
              });
        await audit(
          tx,
          actor,
          'deployment.block_hash',
          `cloud_deployment:${deploymentId}`,
          { status: dep.status, functionSlug: dep.function.slug },
          { status: 'PAUSED', blockedHashes: hashes },
          reason,
        );
        return { deployment: row, blockedHashes: hashes };
      });
      return ok(result);
    }

    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
