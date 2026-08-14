import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/admin/audit — the CloudAuditLog browser (§94). Read-only.
//   ?action=  filter by action prefix (e.g. 'pricing.' or 'app.pause')
//   ?subject= filter by subject substring
export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const action = url.searchParams.get('action')?.trim() ?? '';
    const subject = url.searchParams.get('subject')?.trim() ?? '';
    const { take, skip } = pageParams(req);
    const where: Record<string, unknown> = {};
    if (action) where.action = { startsWith: action };
    if (subject) where.subject = { contains: subject };
    const [rows, total] = await Promise.all([
      prisma.cloudAuditLog.findMany({ where, orderBy: { createdAt: 'desc' }, take, skip }),
      prisma.cloudAuditLog.count({ where }),
    ]);
    return ok({ rows, total });
  } catch (e) {
    return err(e);
  }
}
