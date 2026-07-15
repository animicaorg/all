import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/names/mine -> the caller's registered .anm domains.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const domains = await prisma.anmDomain.findMany({
      where: { ownerId: ctx.accountId },
      orderBy: { registeredAt: 'desc' },
      select: { name: true, kind: true, contentCid: true, agentHandle: true, status: true, expiresAt: true, nodeProviders: true },
    });
    return ok({ domains: jsonSafe(domains.map((d) => ({ ...d, fqdn: `${d.name}.anm` }))) });
  } catch (e) {
    return err(e);
  }
}
