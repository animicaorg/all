import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { normalizeName, registrationFeeNanm } from '@/lib/ans';
import { payNameFee, LedgerError } from '@/lib/ledger';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/names/[name]/renew { years } -> extend expiry, paying the renewal fee.
export async function POST(req: NextRequest, { params }: { params: { name: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');
    const name = normalizeName(params.name);
    const domain = await prisma.anmDomain.findUnique({ where: { name } });
    if (!domain) throw new ApiError(404, 'not_found', 'domain not found');
    if (domain.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    const years = Math.max(1, Math.min(Number((await req.json().catch(() => ({})))?.years ?? 1), 10));
    const fee = registrationFeeNanm(name, years);
    const ref = `anm-renew-${name}-${Date.now()}`;
    try {
      await payNameFee(ctx.accountId, fee, ref, `renew ${name}.anm (${years}y)`);
    } catch (e) {
      if (e instanceof LedgerError) throw new ApiError(402, 'insufficient_funds', `need ${nanmToAnm(fee)} ANM`);
      throw e;
    }
    const base = domain.expiresAt > new Date() ? domain.expiresAt.getTime() : Date.now();
    const expiresAt = new Date(base + years * 365 * 86400_000);
    const updated = await prisma.anmDomain.update({ where: { name }, data: { expiresAt, status: 'ACTIVE' } });
    await prisma.domainEvent.create({ data: { domainId: domain.id, kind: 'renew', detail: `+${years}y` } });
    return ok({ domain: jsonSafe({ ...updated, fqdn: `${name}.anm` }), feeAnm: nanmToAnm(fee) });
  } catch (e) {
    return err(e);
  }
}
