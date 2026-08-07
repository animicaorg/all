import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { rollupFinanceDay, utcDayBounds, previousUtcDay } from '@/scripts/cloud-finance-rollup';
import { adminActor, audit, readJson, optionalString } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/reconcile — reconciliation visibility + FinanceDaily refresh (§91, §92).
//
//   GET  -> recent ReconciliationReport rows (grouped by day) + recent FinanceDaily rows.
//   POST {day?} -> re-run the FinanceDaily rollup for one UTC day (default: yesterday).
//        The rollup is a CACHE refresh computed from authoritative rows — it moves no money.
//        The four verification scopes stay with the animica-cloud-reconcile timer worker,
//        which never runs inside a web request.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const [reports, daily] = await Promise.all([
      prisma.reconciliationReport.findMany({ orderBy: [{ day: 'desc' }, { scope: 'asc' }], take: 60 }),
      prisma.financeDaily.findMany({ orderBy: { day: 'desc' }, take: 30 }),
    ]);
    const mismatched = reports.filter((r) => !r.ok).length;
    return ok({ reports, daily, mismatched });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const day = optionalString(body, 'day', 10) || previousUtcDay();
    if (!utcDayBounds(day)) throw new ApiError(400, 'bad_request', `invalid UTC day '${day}' (YYYY-MM-DD)`);

    const before = await prisma.financeDaily.findUnique({ where: { day } });
    const result = await rollupFinanceDay(day, { write: true });
    const after = await prisma.financeDaily.findUnique({ where: { day } });
    await audit(
      prisma,
      actor,
      'finance.rollup',
      `finance_daily:${day}`,
      before ?? { day, existed: false },
      after ?? { day, written: result.written },
      optionalString(body, 'reason') || 'manual FinanceDaily refresh',
    );
    return ok({ result });
  } catch (e) {
    return err(e);
  }
}
