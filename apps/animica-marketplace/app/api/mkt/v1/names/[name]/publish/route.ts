import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { normalizeName } from '@/lib/ans';
import { putObject, MAX_CONTENT_BYTES } from '@/lib/storage';
import { config } from '@/lib/config';
import { jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/names/[name]/publish { html }  (scope: names, owner-only)
// Publishes a self-contained HTML site to a .anm name. Content is stored CID-addressed and the
// domain's contentCid is updated so the gateway serves it. This node announces itself as a provider.
export async function POST(req: NextRequest, { params }: { params: { name: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');
    const name = normalizeName(params.name);
    const domain = await prisma.anmDomain.findUnique({ where: { name } });
    if (!domain) throw new ApiError(404, 'not_found', `${name}.anm is not registered`);
    if (domain.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');

    const body = await req.json().catch(() => ({}));
    const html = typeof body.html === 'string' ? body.html : '';
    if (!html.trim()) throw new ApiError(400, 'bad_content', 'html body required');
    const bytes = Buffer.from(html, 'utf8');
    if (bytes.length > MAX_CONTENT_BYTES) throw new ApiError(413, 'too_large', `max ${MAX_CONTENT_BYTES} bytes`);

    const { cid, size, deduped } = await putObject(bytes, 'text/html; charset=utf-8', ctx.accountId);
    const providerId = config.nodeId || 'animica.dev';
    const providers = Array.from(new Set([...(domain.nodeProviders ?? []), providerId])).slice(0, 32);
    const updated = await prisma.anmDomain.update({
      where: { name }, data: { contentCid: cid, nodeProviders: providers },
    });
    await prisma.domainEvent.create({ data: { domainId: domain.id, kind: 'update', detail: `publish ${cid.slice(0, 12)}… (${size}B)` } });

    return ok({
      published: true,
      fqdn: `${name}.anm`,
      cid, size, deduped,
      gateway: `${config.baseUrl}/anm/${name}`,
      providers,
      domain: jsonSafe({ ...updated, fqdn: `${name}.anm` }),
    }, { status: 201 });
  } catch (e) {
    return err(e);
  }
}
