import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { normalizeName } from '@/lib/ans';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/names/[name] -> RESOLVE a .anm domain (public). This is the resolution primitive
// the browser extension + gateway call to turn name.anm into content/service pointers.
export async function GET(_req: NextRequest, { params }: { params: { name: string } }) {
  try {
    const name = normalizeName(params.name);
    const domain = await prisma.anmDomain.findUnique({
      where: { name },
      include: { owner: { select: { address: true, displayName: true, isAgent: true } } },
    });
    if (!domain) throw new ApiError(404, 'not_found', `${name}.anm is not registered`);
    let records: any = {};
    try { records = JSON.parse(domain.recordsJson); } catch { /* ignore */ }
    return publicOk({
      fqdn: `${name}.anm`,
      resolved: {
        name, kind: domain.kind, owner: domain.owner.address,
        contentCid: domain.contentCid, agentHandle: domain.agentHandle,
        nodeProviders: domain.nodeProviders, records,
        status: domain.status, expiresAt: domain.expiresAt,
      },
    });
  } catch (e) {
    return err(e);
  }
}

// PATCH /api/mkt/v1/names/[name] -> owner updates records / content / providers (scope: names).
export async function PATCH(req: NextRequest, { params }: { params: { name: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');
    const name = normalizeName(params.name);
    const domain = await prisma.anmDomain.findUnique({ where: { name } });
    if (!domain) throw new ApiError(404, 'not_found', 'domain not found');
    if (domain.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    const body = await req.json();
    const data: any = {};
    if (typeof body.contentCid === 'string') data.contentCid = body.contentCid;
    if (typeof body.agentHandle === 'string') data.agentHandle = body.agentHandle.toLowerCase();
    if (typeof body.records === 'object' && body.records) data.recordsJson = JSON.stringify(body.records);
    if (Array.isArray(body.nodeProviders)) data.nodeProviders = body.nodeProviders.slice(0, 32).map(String);
    const updated = await prisma.anmDomain.update({ where: { name }, data });
    await prisma.domainEvent.create({ data: { domainId: domain.id, kind: 'update', detail: Object.keys(data).join(',') } });
    return ok({ domain: jsonSafe({ ...updated, fqdn: `${name}.anm` }) });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight — native .anm sites resolve names from an opaque origin.
export async function OPTIONS() {
  return publicPreflight();
}
