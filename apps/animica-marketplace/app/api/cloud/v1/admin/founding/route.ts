import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { founding } from '@/lib/cloud/config';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/founding — Founding Developer program review.
//
//   GET  -> applications + seat status (cap from FOUNDING_DEV_SEATS).
//   POST {id, action: 'accept'|'reject'|'revoke', reason}
//
// Accept is the money-bearing action: it assigns the next seat (atomically, capped), grants
// time-boxed Pro (proUntil), the reduced fee window (feeBps/feeUntil) and REAL promotional
// credits (a CloudCredit row drawn down before the ANM balance). All of it in one transaction
// with the CloudAuditLog row; the @unique(seq) constraint makes a seat race fail loudly
// instead of over-allocating.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const status = url.searchParams.get('status') ?? '';
    const { take, skip } = pageParams(req);
    const where = status ? { status } : {};
    const [rows, total, accepted, applied] = await Promise.all([
      prisma.foundingDeveloper.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        include: { account: { select: { address: true, displayName: true, handle: true, createdAt: true } } },
      }),
      prisma.foundingDeveloper.count({ where }),
      prisma.foundingDeveloper.count({ where: { status: 'ACCEPTED' } }),
      prisma.foundingDeveloper.count({ where: { status: 'APPLIED' } }),
    ]);
    return ok({
      rows,
      total,
      seats: {
        cap: founding.seats,
        accepted,
        remaining: Math.max(0, founding.seats - accepted),
        applied,
        benefits: {
          proMonths: founding.proMonths,
          feeBps: founding.feeBps,
          feeMonths: founding.feeMonths,
          creditsNanm: founding.creditsNanm,
        },
      },
    });
  } catch (e) {
    return err(e);
  }
}

function addMonths(d: Date, months: number): Date {
  const out = new Date(d);
  out.setUTCMonth(out.getUTCMonth() + months);
  return out;
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const id = requireString(body, 'id');
    const action = requireString(body, 'action', 40);
    const reason = optionalString(body, 'reason');

    const row = await prisma.foundingDeveloper.findUnique({ where: { id } });
    if (!row) throw new ApiError(404, 'not_found', 'application not found');

    if (action === 'accept') {
      const now = new Date();
      const updated = await prisma.$transaction(async (tx) => {
        // Exactly-once claim: only an APPLIED row can be accepted.
        const claim = await tx.foundingDeveloper.updateMany({
          where: { id, status: 'APPLIED' },
          data: { status: 'ACCEPTED' },
        });
        if (claim.count !== 1) throw new ApiError(409, 'conflict', `application is ${row.status}, not APPLIED`);

        // Seat cap (§ founding): count AFTER the claim so a concurrent accept sees this one.
        const accepted = await tx.foundingDeveloper.count({ where: { status: 'ACCEPTED' } });
        if (accepted > founding.seats) {
          throw new ApiError(409, 'seats_exhausted', `all ${founding.seats} founding seats are taken`);
        }
        const maxSeq = await tx.foundingDeveloper.aggregate({ _max: { seq: true } });
        const seq = (maxSeq._max.seq ?? 0) + 1;
        if (seq > founding.seats) throw new ApiError(409, 'seats_exhausted', `all ${founding.seats} founding seats are taken`);

        const accepted_ = await tx.foundingDeveloper.update({
          where: { id },
          data: {
            seq,
            proUntil: addMonths(now, founding.proMonths),
            feeBps: founding.feeBps,
            feeUntil: addMonths(now, founding.feeMonths),
            creditsNanm: founding.creditsNanm,
            acceptedAt: now,
            acceptedBy: actor,
          },
        });
        if (founding.creditsNanm > 0n) {
          await tx.cloudCredit.create({
            data: {
              accountId: row.accountId,
              grantedNanm: founding.creditsNanm,
              reason: `Founding Developer #${seq}`,
              source: 'founding',
              createdBy: actor,
            },
          });
        }
        await audit(
          tx,
          actor,
          'founding.accept',
          `founding_developer:${id}`,
          { status: 'APPLIED', accountId: row.accountId },
          {
            status: 'ACCEPTED',
            seq,
            proUntil: accepted_.proUntil,
            feeBps: accepted_.feeBps,
            feeUntil: accepted_.feeUntil,
            creditsNanm: founding.creditsNanm,
          },
          reason,
        );
        return accepted_;
      });
      return ok({ row: updated });
    }

    if (action === 'reject') {
      const updated = await prisma.$transaction(async (tx) => {
        const claim = await tx.foundingDeveloper.updateMany({
          where: { id, status: 'APPLIED' },
          data: { status: 'REJECTED', notes: reason ? `${row.notes ? row.notes + '\n' : ''}rejected: ${reason}` : row.notes },
        });
        if (claim.count !== 1) throw new ApiError(409, 'conflict', `application is ${row.status}, not APPLIED`);
        await audit(tx, actor, 'founding.reject', `founding_developer:${id}`, { status: 'APPLIED' }, { status: 'REJECTED' }, reason);
        return tx.foundingDeveloper.findUnique({ where: { id } });
      });
      return ok({ row: updated });
    }

    if (action === 'revoke') {
      if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required to revoke founding benefits");
      const updated = await prisma.$transaction(async (tx) => {
        const claim = await tx.foundingDeveloper.updateMany({
          where: { id, status: 'ACCEPTED' },
          data: { status: 'REVOKED', revokedAt: new Date(), revokedReason: reason, featured: false },
        });
        if (claim.count !== 1) throw new ApiError(409, 'conflict', `application is ${row.status}, not ACCEPTED`);
        // Founding credits stop being spendable immediately; already-spent amounts stand
        // (history is never rewritten, §88).
        const credits = await tx.cloudCredit.updateMany({
          where: { accountId: row.accountId, source: 'founding', revokedAt: null },
          data: { revokedAt: new Date() },
        });
        await audit(
          tx,
          actor,
          'founding.revoke',
          `founding_developer:${id}`,
          { status: 'ACCEPTED', seq: row.seq, feeBps: row.feeBps, proUntil: row.proUntil },
          { status: 'REVOKED', creditsRevoked: credits.count },
          reason,
        );
        return tx.foundingDeveloper.findUnique({ where: { id } });
      });
      return ok({ row: updated });
    }

    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
