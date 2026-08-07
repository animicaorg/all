import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { normalizeName } from '@/lib/ans';
import { ensureAccount } from '@/lib/accounts';
import { isAnimicaAddress } from '@/lib/address';
import { jsonSafe } from '@/lib/nanm';
import { canCreateDeployment } from '@/lib/plan';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/names/[name]/transfer { toAddress } -> reassign ownership (scope: names).
export async function POST(req: NextRequest, { params }: { params: { name: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'names');
    const name = normalizeName(params.name);
    const domain = await prisma.anmDomain.findUnique({ where: { name } });
    if (!domain) throw new ApiError(404, 'not_found', 'domain not found');
    if (domain.ownerId !== ctx.accountId) throw new ApiError(403, 'forbidden', 'not the owner');
    const body = await req.json();
    const to = String(body.toAddress ?? '').trim().toLowerCase();
    if (!isAnimicaAddress(to)) throw new ApiError(400, 'bad_address', 'valid anim1 recipient required');
    const recipient = await ensureAccount(to);
    // A transfer is unsolicited: the sender picks the recipient. If the incoming name would
    // put the recipient over their deployment limit, it must arrive SHELVED rather than
    // ACTIVE — otherwise the enforcement sweep's keep-oldest rule could take one of the
    // recipient's OWN live sites offline to make room for a name they never asked for.
    // They activate it themselves from /settings/billing (or by upgrading).
    const { ok: hasSlot } = await canCreateDeployment(recipient.id);
    const landing = domain.status === 'ACTIVE' && !hasSlot ? 'SUSPENDED' : domain.status;
    const updated = await prisma.anmDomain.update({
      where: { name },
      data: { ownerId: recipient.id, status: landing },
    });
    await prisma.domainEvent.create({
      data: {
        domainId: domain.id,
        kind: 'transfer',
        detail: `-> ${to.slice(0, 16)}…${landing === 'SUSPENDED' && domain.status === 'ACTIVE' ? ' (shelved: recipient at plan limit)' : ''}`,
      },
    });
    return ok({
      domain: jsonSafe({ ...updated, fqdn: `${name}.anm` }),
      transferredTo: to,
      ...(landing === 'SUSPENDED' && domain.status === 'ACTIVE'
        ? { note: 'recipient is at their .anm deployment limit — the name arrived suspended and can be activated from /settings/billing' }
        : {}),
    });
  } catch (e) {
    return err(e);
  }
}
