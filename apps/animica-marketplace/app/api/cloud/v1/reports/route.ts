import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, authenticate, ApiError } from '@/lib/api';
import { anonIdentity, enforceBurst } from '@/lib/cloud/ratelimit';
import { str, requireStr } from '../apps/_shared';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// POST /api/cloud/v1/reports — abuse report for an app, function or developer.
//
// Anonymous reporting is allowed (abuse is often reported by people without accounts), but it
// is never a free-for-all: an in-process burst limiter, a durable 24h per-reporter cap, and
// OPEN-report dedup all apply. Reports land in CloudReport for the admin review queue.

const RATE_PER_MIN = Math.max(1, Number(process.env.CLOUD_REPORT_RATE_PER_MIN ?? 3));
const MAX_PER_DAY = Math.max(1, Number(process.env.CLOUD_REPORT_MAX_PER_DAY ?? 10));

const SUBJECT_KINDS = ['app', 'function', 'developer'] as const;

async function resolveSubject(kind: string, ref: string): Promise<{ id: string; label: string }> {
  if (kind === 'app') {
    const app = await prisma.cloudApp.findFirst({
      where: { OR: [{ id: ref }, { slug: ref.toLowerCase() }] },
      select: { id: true, slug: true },
    });
    if (!app) throw new ApiError(404, 'not_found', 'no such app');
    return { id: app.id, label: app.slug };
  }
  if (kind === 'function') {
    const fn = await prisma.cloudFunction.findUnique({ where: { id: ref }, select: { id: true, slug: true } });
    if (!fn) throw new ApiError(404, 'not_found', 'no such function (pass the function id)');
    return { id: fn.id, label: fn.slug };
  }
  const account = await prisma.account.findFirst({
    where: { OR: [{ id: ref }, { handle: ref.toLowerCase() }, { address: ref }] },
    select: { id: true, handle: true },
  });
  if (!account) throw new ApiError(404, 'not_found', 'no such developer');
  return { id: account.id, label: account.handle ?? account.id };
}

export async function POST(req: NextRequest) {
  try {
    let accountId: string | null = null;
    try {
      const auth = await authenticate(req);
      accountId = auth?.accountId ?? null;
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) throw e;
    }
    const reporterId = accountId ?? anonIdentity(req);
    enforceBurst(reporterId, { perMin: RATE_PER_MIN, scope: 'report' });

    const body = await req.json().catch(() => ({}));
    const kind = str(body?.subjectKind, 12).toLowerCase();
    if (!(SUBJECT_KINDS as readonly string[]).includes(kind)) {
      throw new ApiError(400, 'bad_request', `subjectKind must be ${SUBJECT_KINDS.join(' | ')}`);
    }
    const ref = requireStr(body?.subjectId, 'subjectId', 100);
    const reason = requireStr(body?.reason, 'reason', 200, 3);
    const detail = str(body?.detail, 4000);

    const subject = await resolveSubject(kind, ref);

    // Durable flood guard: one identity files at most MAX_PER_DAY reports per 24h.
    const since = new Date(Date.now() - 24 * 3600 * 1000);
    const recent = await prisma.cloudReport.count({ where: { reporterId, createdAt: { gte: since } } });
    if (recent >= MAX_PER_DAY) {
      throw new ApiError(429, 'rate_limited', 'report limit reached for today — existing reports are already in the review queue');
    }

    // Dedup: one OPEN report per (reporter, subject). A duplicate acknowledges the original.
    const existing = await prisma.cloudReport.findFirst({
      where: { reporterId, subjectKind: kind, subjectId: subject.id, status: 'OPEN' },
    });
    if (existing) {
      return ok({
        reported: true,
        duplicate: true,
        report: { id: existing.id, subjectKind: kind, subject: subject.label, status: existing.status, createdAt: existing.createdAt },
        message: 'you already have an open report for this — it is in the review queue',
      });
    }

    const report = await prisma.cloudReport.create({
      data: { subjectKind: kind, subjectId: subject.id, reporterId, reason, detail },
    });
    return ok(
      {
        reported: true,
        duplicate: false,
        report: { id: report.id, subjectKind: kind, subject: subject.label, status: report.status, createdAt: report.createdAt },
        message: 'thank you — the report is in the moderation queue',
      },
      { status: 201 },
    );
  } catch (e) {
    return err(e);
  }
}
