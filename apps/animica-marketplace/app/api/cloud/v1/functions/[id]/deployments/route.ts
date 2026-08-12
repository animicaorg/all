import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { loadOwnedFunction, pageParams, serializeDeployment } from '../../shared';

export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/functions/[id]/deployments?status=&limit=&cursor=
//
// Full deployment history for the owner's function, newest first, including the on-chain
// anchor record (DEPLOY txid, inclusion height, confirmation depth, DA blob id) and the
// persisted per-step deployment log. Deployments are ANCHORED on-chain and EXECUTED
// off-chain in a hardened container; unanchored deployments carry the recorded reason
// in their log instead of a fabricated txid.

const STATUSES = [
  'DRAFT',
  'VALIDATING',
  'BUILDING',
  'AWAITING_SIGNATURE',
  'BROADCASTING',
  'CONFIRMING',
  'ACTIVE',
  'FAILED',
  'PAUSED',
  'ARCHIVED',
];

export async function GET(req: NextRequest, route: { params: { id: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const fn = await loadOwnedFunction(route.params.id, ctx.accountId);

    const sp = req.nextUrl.searchParams;
    const { take, cursor } = pageParams(sp);
    const status = sp.get('status')?.toUpperCase() ?? null;
    if (status && !STATUSES.includes(status)) {
      throw new ApiError(400, 'bad_request', `status must be one of ${STATUSES.join(', ')}`);
    }

    const where: any = { functionId: fn.id };
    if (status) where.status = status;

    const rows = await prisma.cloudDeployment.findMany({
      where,
      orderBy: [{ createdAt: 'desc' }, { id: 'desc' }],
      ...(cursor ? { cursor: { id: cursor }, skip: 1 } : {}),
      take,
      include: { version: { select: { version: true } } },
    });

    return ok({
      deployments: rows.map(serializeDeployment),
      nextCursor: rows.length === take ? rows[rows.length - 1].id : null,
    });
  } catch (e) {
    return err(e);
  }
}
