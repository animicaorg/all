import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/reports — abuse-report triage (§39).
//   GET  ?status=
//   POST {id, action: 'reviewing'|'action'|'dismiss', resolution}
// Resolving a report records WHO decided WHAT and WHY; any enforcement (pausing the app,
// suspending the developer, blocking the hash) is done through the dedicated audited actions.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const status = url.searchParams.get('status') ?? '';
    const { take, skip } = pageParams(req);
    const where = status ? { status } : { status: { in: ['OPEN', 'REVIEWING'] } };
    const [rows, total, openCount] = await Promise.all([
      prisma.cloudReport.findMany({ where, orderBy: { createdAt: 'desc' }, take, skip }),
      prisma.cloudReport.count({ where }),
      prisma.cloudReport.count({ where: { status: { in: ['OPEN', 'REVIEWING'] } } }),
    ]);

    // Resolve subjects for display (apps/functions by id; developers are account ids).
    const appIds = rows.filter((r) => r.subjectKind === 'app').map((r) => r.subjectId);
    const fnIds = rows.filter((r) => r.subjectKind === 'function').map((r) => r.subjectId);
    const devIds = rows.filter((r) => r.subjectKind === 'developer').map((r) => r.subjectId);
    const [apps, fns, devs] = await Promise.all([
      appIds.length ? prisma.cloudApp.findMany({ where: { id: { in: appIds } }, select: { id: true, slug: true, name: true, status: true } }) : [],
      fnIds.length ? prisma.cloudFunction.findMany({ where: { id: { in: fnIds } }, select: { id: true, slug: true, name: true, status: true } }) : [],
      devIds.length ? prisma.account.findMany({ where: { id: { in: devIds } }, select: { id: true, address: true, handle: true } }) : [],
    ]);
    const subjects: Record<string, unknown> = {};
    for (const a of apps) subjects[`app:${a.id}`] = a;
    for (const f of fns) subjects[`function:${f.id}`] = f;
    for (const d of devs) subjects[`developer:${d.id}`] = d;

    return ok({ rows, total, openCount, subjects });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const id = requireString(body, 'id');
    const action = requireString(body, 'action', 40);
    const resolution = optionalString(body, 'resolution');

    const report = await prisma.cloudReport.findUnique({ where: { id } });
    if (!report) throw new ApiError(404, 'not_found', 'report not found');

    const target: Record<string, string> = { reviewing: 'REVIEWING', action: 'ACTIONED', dismiss: 'DISMISSED' };
    const to = target[action];
    if (!to) throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
    if ((to === 'ACTIONED' || to === 'DISMISSED') && !resolution) {
      throw new ApiError(400, 'bad_request', "'resolution' is required when closing a report");
    }
    if (report.status === to) throw new ApiError(409, 'conflict', `report is already ${to}`);

    const updated = await prisma.$transaction(async (tx) => {
      const row = await tx.cloudReport.update({
        where: { id },
        data: {
          status: to,
          ...(to === 'ACTIONED' || to === 'DISMISSED'
            ? { resolution, resolvedBy: actor, resolvedAt: new Date() }
            : {}),
        },
      });
      await audit(
        tx,
        actor,
        `report.${action}`,
        `cloud_report:${id}`,
        { status: report.status, subjectKind: report.subjectKind, subjectId: report.subjectId },
        { status: to, resolution },
        resolution,
      );
      return row;
    });
    return ok({ report: updated });
  } catch (e) {
    return err(e);
  }
}
