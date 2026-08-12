import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency } from '@/lib/api';
import { limits } from '@/lib/cloud/config';
import { enforceBurst } from '@/lib/cloud/ratelimit';
import { createVersion } from '@/lib/cloud/deploy';
import { loadOwnedFunction, pageParams } from '../../shared';

export const dynamic = 'force-dynamic';
// Deploys wait on validation + DA put + anchor broadcast + bounded inclusion wait.
export const maxDuration = 300;

// GET  /api/cloud/v1/functions/[id]/versions  -> immutable version history (append-only, §6)
// POST /api/cloud/v1/functions/[id]/versions  -> snapshot new source as version max+1 AND
//                                                deploy it (validate -> anchor -> ACTIVE)

export async function GET(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);

    const sp = req.nextUrl.searchParams;
    const { take, cursor } = pageParams(sp);
    const includeSource = sp.get('source') === '1';

    const rows = await prisma.cloudFunctionVersion.findMany({
      where: { functionId: fn.id },
      orderBy: { version: 'desc' },
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      take,
    });

    return ok({
      currentVersion: fn.currentVersion,
      versions: rows.map((v) => {
        let validation: unknown = null;
        try {
          validation = JSON.parse(v.validationJson || 'null');
        } catch {
          validation = null;
        }
        return {
          id: v.id,
          version: v.version,
          isCurrent: v.version === fn.currentVersion,
          sizeBytes: v.sizeBytes,
          sourceSha3: v.sourceSha3,
          artifactSha3: v.artifactSha3,
          entrypoint: v.entrypoint,
          packages: v.packages,
          estimateNanm: v.estimateNanm,
          validation,
          createdById: v.createdById,
          createdAt: v.createdAt,
          ...(includeSource ? { source: v.source } : {}),
        };
      }),
      nextCursor: rows.length === take ? rows[rows.length - 1].id : null,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'publish');
    // Burst limiter in front of the durable per-day deploy quota createVersion enforces.
    enforceBurst(ctx.accountId, { perMin: limits.rateDeployPerHour, scope: 'deploy' });

    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);
    const body = await req.json().catch(() => {
      throw new ApiError(400, 'bad_json', 'request body must be valid JSON');
    });
    if (typeof body.source !== 'string' || !body.source.trim()) {
      throw new ApiError(400, 'bad_request', 'source (Python text) is required');
    }

    return await withIdempotency(req, ctx, body, async () => {
      // createVersion enforces ownership, size, entitlements (slots + daily deploy quota +
      // marketplace_publishing for PUBLIC), static validation, denylist, hashing, anchoring
      // and activation — see lib/cloud/deploy.ts.
      const result = await createVersion({
        functionId: fn.id,
        source: body.source,
        entrypoint: body.entrypoint != null ? String(body.entrypoint) : undefined,
        packages: body.packages,
        actorAccountId: ctx.accountId,
      });
      return { status: 201, data: result };
    });
  } catch (e) {
    return err(e);
  }
}
