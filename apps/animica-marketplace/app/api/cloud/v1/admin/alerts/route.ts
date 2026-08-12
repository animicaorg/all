import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/alerts — FinanceAlert triage (§90).
//   GET  ?state=open|resolved|all
//   POST {id, action: 'resolve'|'reopen', reason}

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const state = url.searchParams.get('state') ?? 'open';
    const kind = url.searchParams.get('kind') ?? '';
    const { take, skip } = pageParams(req);
    const where: Record<string, unknown> = {};
    if (state === 'open') where.resolvedAt = null;
    else if (state === 'resolved') where.resolvedAt = { not: null };
    if (kind) where.kind = kind;
    const [rows, total, openCount] = await Promise.all([
      prisma.financeAlert.findMany({ where, orderBy: { createdAt: 'desc' }, take, skip }),
      prisma.financeAlert.count({ where }),
      prisma.financeAlert.count({ where: { resolvedAt: null } }),
    ]);
    return ok({ rows, total, openCount });
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
    const reason = optionalString(body, 'reason');

    const alert = await prisma.financeAlert.findUnique({ where: { id } });
    if (!alert) throw new ApiError(404, 'not_found', 'alert not found');

    if (action === 'resolve') {
      if (alert.resolvedAt) throw new ApiError(409, 'conflict', 'alert is already resolved');
      const updated = await prisma.$transaction(async (tx) => {
        const row = await tx.financeAlert.update({ where: { id }, data: { resolvedAt: new Date(), resolvedBy: actor } });
        await audit(tx, actor, 'alert.resolve', `finance_alert:${id}`, { resolvedAt: null, kind: alert.kind, title: alert.title }, { resolvedAt: row.resolvedAt }, reason);
        return row;
      });
      return ok({ alert: updated });
    }
    if (action === 'reopen') {
      if (!alert.resolvedAt) throw new ApiError(409, 'conflict', 'alert is not resolved');
      const updated = await prisma.$transaction(async (tx) => {
        const row = await tx.financeAlert.update({ where: { id }, data: { resolvedAt: null, resolvedBy: null } });
        await audit(tx, actor, 'alert.reopen', `finance_alert:${id}`, { resolvedAt: alert.resolvedAt }, { resolvedAt: null }, reason);
        return row;
      });
      return ok({ alert: updated });
    }
    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
