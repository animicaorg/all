import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/vpn/denylist  (scope: vpn)
// Current denylist entries (cidr | peer) for exits to pull and refuse routing to.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'vpn');

    const url = new URL(req.url);
    const kind = url.searchParams.get('kind');
    const where: any = {};
    if (kind === 'cidr' || kind === 'peer') where.kind = kind;

    const entries = await prisma.vpnDenylistEntry.findMany({
      where,
      orderBy: { createdAt: 'desc' },
      take: 1000,
      select: { kind: true, value: true, reason: true, createdAt: true },
    });
    return ok({ entries: jsonSafe(entries), count: entries.length });
  } catch (e) {
    return err(e);
  }
}
