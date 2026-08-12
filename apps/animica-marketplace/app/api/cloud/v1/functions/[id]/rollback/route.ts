import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { flags, limits } from '@/lib/cloud/config';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { rollbackTo } from '@/lib/cloud/deploy';
import { loadOwnedFunction, serializeDeployment } from '../../shared';

export const dynamic = 'force-dynamic';
export const maxDuration = 300;

// POST /api/cloud/v1/functions/[id]/rollback  { version: number }
//
// A rollback is a NEW deployment pointing at an OLD immutable version snapshot — history is
// append-only and never rewritten (lib/cloud/deploy.ts rollbackTo). It consumes the same daily
// deploy quota as any other deployment.

export async function POST(req: NextRequest, route: { params: { id: string } }) {
  try {
    if (!flags.pythonCloud) throw new ApiError(503, 'disabled', 'Python Cloud deployments are temporarily disabled');
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    enforceBurst(ctx.accountId, { perMin: limits.rateDeployPerHour, scope: 'deploy' });

    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });
    const version = Number(body.version);
    if (!Number.isInteger(version) || version < 1) {
      throw new ApiError(400, 'bad_request', 'version must be a positive integer');
    }

    return await withIdempotency(req, ctx, body, async () => {
      const finished = await rollbackTo(fn.id, version, ctx.accountId);
      const withVersion = await prisma.cloudDeployment.findUnique({
        where: { id: finished.id },
        include: { version: { select: { version: true } } },
      });
      return { status: 201, data: { deployment: serializeDeployment(withVersion!), rolledBackTo: version } };
    });
  } catch (e) {
    return err(e);
  }
}
