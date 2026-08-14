import { NextRequest } from 'next/server';
import { Prisma } from '@prisma/client';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, requireScope, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { founding } from '@/lib/cloud/config';
import { str } from '../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// The Founding Developer program (first N seats, real benefits, §88-snapshotted fee rate).
//
//   POST /api/cloud/v1/founding                      apply (and auto-accept when configured)
//   POST /api/cloud/v1/founding {action, accountId}  admin accept/reject (autoAccept off)
//   GET  /api/cloud/v1/founding                      my application status + live seat counts
//
// Seat assignment is SERIALIZED on a Postgres advisory transaction lock: every acceptance
// takes pg_advisory_xact_lock() before reading MAX(seq), so 101 concurrent applications can
// never mint seat 101 — the lock holds until the accepting transaction commits, and seq is
// additionally UNIQUE at the schema level as a belt-and-braces guarantee.

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/;

function addMonths(from: Date, months: number): Date {
  const d = new Date(from.getTime());
  d.setUTCMonth(d.getUTCMonth() + months);
  return d;
}

function applicationView(fd: {
  status: string;
  seq: number | null;
  proUntil: Date | null;
  feeBps: number;
  feeUntil: Date | null;
  creditsNanm: bigint;
  createdAt: Date;
  acceptedAt: Date | null;
  revokedAt: Date | null;
}) {
  const accepted = fd.status === 'ACCEPTED' && !fd.revokedAt;
  return {
    status: fd.status,
    seq: accepted ? fd.seq : null,
    appliedAt: fd.createdAt,
    acceptedAt: fd.acceptedAt,
    benefits: accepted
      ? {
          proUntil: fd.proUntil,
          feeBps: fd.feeBps,
          feeUntil: fd.feeUntil,
          creditsNanm: fd.creditsNanm,
        }
      : null,
  };
}

async function programInfo() {
  const seatsClaimed = await prisma.foundingDeveloper.count({ where: { seq: { not: null } } });
  return {
    seatsTotal: founding.seats,
    seatsClaimed,
    seatsRemaining: Math.max(0, founding.seats - seatsClaimed),
    benefits: {
      proMonths: founding.proMonths,
      feeBps: founding.feeBps,
      feeMonths: founding.feeMonths,
      creditsNanm: founding.creditsNanm,
    },
    autoAccept: founding.autoAccept,
  };
}

type AcceptOutcome =
  | { accepted: true; already: boolean; fd: any }
  | { accepted: false; full: true; fd: any };

/** Atomic, serialized seat claim. Grants seq + Pro window + reduced fee + credits, exactly once. */
async function acceptFounding(accountId: string, actor: string): Promise<AcceptOutcome> {
  return prisma.$transaction(async (tx: Prisma.TransactionClient): Promise<AcceptOutcome> => {
    // Serialize ALL seat claims. The lock releases when this transaction commits or aborts.
    await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtextextended('animica_founding_seats', 0))`;

    const fd = await tx.foundingDeveloper.findUnique({ where: { accountId } });
    if (!fd) throw new ApiError(404, 'not_found', 'no application on file for this account');
    if (fd.status === 'ACCEPTED' && !fd.revokedAt) return { accepted: true, already: true, fd };
    if (fd.status === 'REVOKED' || fd.revokedAt) {
      throw new ApiError(409, 'revoked', 'this seat was revoked and cannot be re-granted automatically');
    }

    const maxSeq = await tx.foundingDeveloper.aggregate({ _max: { seq: true } });
    const seq = (maxSeq._max.seq ?? 0) + 1;
    if (seq > founding.seats) return { accepted: false, full: true, fd };

    const now = new Date();
    const proUntil = addMonths(now, founding.proMonths);
    const feeUntil = addMonths(now, founding.feeMonths);

    // Conditional claim — under the advisory lock this cannot race, and the status guard means
    // a concurrent duplicate in the same window still cannot double-grant.
    const claimed = await tx.foundingDeveloper.updateMany({
      where: { id: fd.id, status: { in: ['APPLIED'] }, seq: null },
      data: {
        seq,
        status: 'ACCEPTED',
        proUntil,
        feeBps: founding.feeBps,
        feeUntil,
        creditsNanm: founding.creditsNanm,
        acceptedAt: now,
        acceptedBy: actor,
      },
    });
    if (claimed.count !== 1) throw new ApiError(409, 'conflict', 'the application changed state — retry');

    // The execution-credit grant. Drawn down before real balance; platform absorbs the cost
    // as cogsPromoNanm at settlement time, so this never mints withdrawable ANM.
    await tx.cloudCredit.create({
      data: {
        accountId,
        grantedNanm: founding.creditsNanm,
        reason: `Founding Developer seat #${seq}`,
        source: 'founding',
        createdBy: actor,
      },
    });

    await tx.cloudAuditLog.create({
      data: {
        actor,
        action: 'founding.accept',
        subject: accountId,
        after: JSON.stringify({
          seq,
          proUntil: proUntil.toISOString(),
          feeBps: founding.feeBps,
          feeUntil: feeUntil.toISOString(),
          creditsNanm: founding.creditsNanm.toString(),
        }),
        reason: 'Founding Developer seat granted',
      },
    });

    const updated = await tx.foundingDeveloper.findUnique({ where: { id: fd.id } });
    return { accepted: true, already: false, fd: updated! };
  });
}

export async function GET(req: NextRequest) {
  try {
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key');
    requireScope(auth, 'read');
    const [fd, program] = await Promise.all([
      prisma.foundingDeveloper.findUnique({ where: { accountId: auth.accountId } }),
      programInfo(),
    ]);
    return ok({ application: fd ? applicationView(fd) : null, program });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(() => ({}));
    const action = str(body?.action, 20).toLowerCase();

    // --- admin path: accept/reject a specific application (required when autoAccept is off) ---
    if (action === 'accept' || action === 'reject') {
      const admin = await requireAdmin(req);
      const actor = admin.via === 'account' ? `admin:${admin.accountId}` : 'admin:token';
      const accountId = str(body?.accountId, 64);
      if (!accountId) throw new ApiError(400, 'bad_request', 'accountId is required');

      if (action === 'reject') {
        const fd = await prisma.foundingDeveloper.findUnique({ where: { accountId } });
        if (!fd) throw new ApiError(404, 'not_found', 'no application on file');
        if (fd.status === 'ACCEPTED') throw new ApiError(409, 'conflict', 'already accepted — revocation is a separate admin action');
        const updated = await prisma.foundingDeveloper.update({
          where: { id: fd.id },
          data: { status: 'REJECTED', notes: str(body?.notes, 1000) || fd.notes },
        });
        await prisma.cloudAuditLog.create({
          data: { actor, action: 'founding.reject', subject: accountId, reason: str(body?.notes, 1000) },
        });
        return ok({ application: applicationView(updated), program: await programInfo() });
      }

      const outcome = await acceptFounding(accountId, actor);
      if (!outcome.accepted) {
        throw new ApiError(409, 'program_full', `all ${founding.seats} founding seats are taken`);
      }
      return ok({ application: applicationView(outcome.fd), already: outcome.already, program: await programInfo() });
    }

    // --- developer path: apply ------------------------------------------------------------
    const auth = await authenticate(req);
    if (!auth) throw new ApiError(401, 'unauthorized', 'sign in or use an API key to apply');
    requireScope(auth, 'use');

    const email = str(body?.email, 200);
    if (email && !EMAIL_RE.test(email)) throw new ApiError(400, 'invalid', 'email is not valid');
    const pitch = str(body?.pitch, 4000);

    const account = await prisma.account.findUnique({
      where: { id: auth.accountId },
      select: { handle: true },
    });

    const existing = await prisma.foundingDeveloper.findUnique({ where: { accountId: auth.accountId } });
    let fd =
      existing ??
      (await prisma.foundingDeveloper.create({
        data: {
          accountId: auth.accountId,
          handle: account?.handle ?? '',
          email,
          pitch,
          status: 'APPLIED',
        },
      }));
    // Let a still-pending applicant refresh their pitch/email.
    if (existing && existing.status === 'APPLIED' && (email || pitch)) {
      fd = await prisma.foundingDeveloper.update({
        where: { id: existing.id },
        data: { ...(email ? { email } : {}), ...(pitch ? { pitch } : {}) },
      });
    }

    if (fd.status === 'APPLIED' && founding.autoAccept) {
      const outcome = await acceptFounding(auth.accountId, 'system:auto-accept');
      if (outcome.accepted) {
        return ok(
          { application: applicationView(outcome.fd), program: await programInfo() },
          { status: outcome.already ? 200 : 201 },
        );
      }
      // Seats are gone: the application stays on file for any future expansion.
      return ok({
        application: applicationView(outcome.fd),
        waitlisted: true,
        program: await programInfo(),
        message: `all ${founding.seats} founding seats are taken — your application is on file`,
      });
    }

    return ok(
      {
        application: applicationView(fd),
        program: await programInfo(),
        message:
          fd.status === 'APPLIED'
            ? 'application received — acceptance is reviewed by the Animica team'
            : undefined,
      },
      { status: existing ? 200 : 201 },
    );
  } catch (e) {
    return err(e);
  }
}
