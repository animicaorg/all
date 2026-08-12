import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { flags, limits } from '@/lib/cloud/config';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { deployVersion } from '@/lib/cloud/deploy';
import { loadOwnedFunction, enforceDeployEntitlements, serializeDeployment } from '../../shared';

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/cloud/v1/functions/[id]/deploy
//
// Deploy an EXISTING version (no new source snapshot):
//   { version?: number }        -> fresh deployment of that version (default: newest version)
//   { deploymentId?: string }   -> resume a stalled/failed deployment attempt
//
// Deployments are ANCHORED on-chain (source hash + artifact hash + DA blob id inside a signed
// DEPLOY tx) and EXECUTED off-chain in a hardened container — deployVersion() records exactly
// which of those steps happened in the deployment log.

export async function POST(req: NextRequest, route: { params: { id: string } }) {
  try {
    if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Python Cloud deployments are temporarily disabled');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    enforceBurst(ctx.accountId, { perMin: limits.rateDeployPerHour, scope: 'deploy' });

    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);
    if (fn.suspendedAt) throw new ApiError(403, 'suspended', fn.suspendedReason || 'this function has been suspended');
    const body = await req.json().catch(() => ({}));

    return await withIdempotency(req, ctx, body, async () => {
      // Resume path: drive an existing deployment row forward (idempotent when already ACTIVE).
      if (body.deploymentId != null) {
        const dep = await prisma.cloudDeployment.findUnique({
          where: { id: String(body.deploymentId) },
          select: { id: true, functionId: true, status: true },
        });
        if (!dep || dep.functionId !== fn.id) throw new ApiError(404, 'not_found', 'deployment not found');
        if (dep.status !== 'ACTIVE') await enforceDeployEntitlements(fn);
        const finished = await deployVersion(dep.id);
        const withVersion = await prisma.cloudDeployment.findUnique({
          where: { id: finished.id },
          include: { version: { select: { version: true } } },
        });
        return { status: 200, data: { deployment: serializeDeployment(withVersion!) } };
      }

      // Fresh deployment of an existing immutable version snapshot.
      const version =
        body.version != null
          ? await prisma.cloudFunctionVersion.findUnique({
              where: { functionId_version: { functionId: fn.id, version: Number(body.version) } },
            })
          : await prisma.cloudFunctionVersion.findFirst({
              where: { functionId: fn.id },
              orderBy: { version: 'desc' },
            });
      if (!version) {
        throw new ApiError(
          404,
          'no_version',
          body.version != null
            ? `version ${body.version} does not exist for this function`
            : 'this function has no versions yet — POST /versions with {source} first',
        );
      }

      // Same entitlement gates createVersion applies: publishing rights, function slot,
      // daily deploy quota (all live counts).
      await enforceDeployEntitlements(fn);

      const dep = await prisma.cloudDeployment.create({
        data: {
          functionId: fn.id,
          versionId: version.id,
          status: 'DRAFT',
          logsJson: JSON.stringify([
            {
              ts: new Date().toISOString(),
              level: 'info',
              message: `redeploy requested for version ${version.version} (current is ${fn.currentVersion})`,
            },
          ]),
        },
      });
      const finished = await deployVersion(dep.id);
      const withVersion = await prisma.cloudDeployment.findUnique({
        where: { id: finished.id },
        include: { version: { select: { version: true } } },
      });
      return { status: 201, data: { deployment: serializeDeployment(withVersion!) } };
    });
  } catch (e) {
    return err(e);
  }
}
