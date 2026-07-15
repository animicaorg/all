import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError, withIdempotency, publicOk, publicPreflight } from '@/lib/api';
import { prisma } from '@/lib/db';
import { validateName, normalizeName, registrationFeeNanm } from '@/lib/ans';
import { payNameFee, LedgerError } from '@/lib/ledger';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/names?search=&kind= -> the .anm search index (Google-like).
export async function GET(req: NextRequest) {
  try {
    const sp = req.nextUrl.searchParams;
    const search = normalizeName(sp.get('search') ?? '');
    const kind = sp.get('kind') ?? '';
    const where: any = { status: 'ACTIVE' };
    if (kind) where.kind = kind;
    if (search) where.OR = [
      { name: { contains: search, mode: 'insensitive' } },
      { recordsJson: { contains: search, mode: 'insensitive' } },
    ];
    const domains = await prisma.anmDomain.findMany({
      where, take: 60, orderBy: { registeredAt: 'desc' },
      select: {
        name: true, kind: true, contentCid: true, agentHandle: true, recordsJson: true,
        nodeProviders: true, registeredAt: true, expiresAt: true,
        owner: { select: { address: true, displayName: true, isAgent: true } },
      },
    });
    const total = await prisma.anmDomain.count({ where: { status: 'ACTIVE' } });
    // publicOk: native .anm sites (opaque origin under the gateway sandbox) query this.
    return publicOk({ total, results: jsonSafe(domains.map((d) => ({ ...d, fqdn: `${d.name}.anm` }))) });
  } catch (e) {
    return err(e);
  }
}

// POST /api/mkt/v1/names { name, years?, kind?, agentHandle?, records? }
// Register a .anm domain (scope: names). Pays the registration fee from balance. This is how an
// autonomous agent claims its own identity on the Animica Internet.
export async function POST(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');
    const body = await req.json();
    const name = normalizeName(body.name ?? '');
    const v = validateName(name);
    if (!v.ok) throw new ApiError(400, 'bad_name', v.reason!);

    return await withIdempotency(req, ctx, body, async () => {
      const existing = await prisma.anmDomain.findUnique({ where: { name } });
      if (existing && existing.status !== 'EXPIRED') throw new ApiError(409, 'taken', `${name}.anm is already registered`);

      const years = Math.max(1, Math.min(Number(body.years ?? 1), 10));
      const fee = registrationFeeNanm(name, years);
      const ref = `anm-reg-${name}-${Date.now()}`;
      try {
        await payNameFee(ctx.accountId, fee, ref, `register ${name}.anm (${years}y)`);
      } catch (e) {
        if (e instanceof LedgerError) throw new ApiError(402, 'insufficient_funds', `need ${nanmToAnm(fee)} ANM to register ${name}.anm`);
        throw e;
      }

      const kind = ['app', 'agent', 'personal', 'business', 'ai'].includes(body.kind) ? body.kind : 'app';
      const expiresAt = new Date(Date.now() + years * 365 * 86400_000);
      const records = typeof body.records === 'object' && body.records ? JSON.stringify(body.records) : '{}';

      // If registering an agent domain, link the caller's agent handle when present.
      let agentHandle: string | null = body.agentHandle ? String(body.agentHandle).toLowerCase() : null;
      if (kind === 'agent' && !agentHandle) {
        const prof = await prisma.agentProfile.findUnique({ where: { accountId: ctx.accountId }, select: { handle: true } });
        agentHandle = prof?.handle ?? null;
      }

      const domain = existing
        ? await prisma.anmDomain.update({
            where: { name },
            data: { ownerId: ctx.accountId, kind, expiresAt, status: 'ACTIVE', recordsJson: records, agentHandle, contentCid: null },
          })
        : await prisma.anmDomain.create({
            data: { name, ownerId: ctx.accountId, kind, expiresAt, recordsJson: records, agentHandle },
          });
      await prisma.domainEvent.create({ data: { domainId: domain.id, kind: 'register', detail: `${years}y, fee ${nanmToAnm(fee)} ANM` } });
      // Link an agent profile to its domain if applicable.
      if (agentHandle) {
        await prisma.agentProfile.updateMany({ where: { handle: agentHandle, accountId: ctx.accountId }, data: { anmDomain: `${name}.anm` } }).catch(() => {});
      }
      return { status: 201, data: { domain: jsonSafe({ ...domain, fqdn: `${name}.anm` }), feeAnm: nanmToAnm(fee) } };
    });
  } catch (e) {
    return err(e);
  }
}

// CORS preflight for the public search (native .anm sites are on an opaque origin).
export async function OPTIONS() {
  return publicPreflight();
}
