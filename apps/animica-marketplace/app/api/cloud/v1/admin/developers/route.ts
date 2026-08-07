import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/developers — developer accounts on the Python Cloud (§39).
//   GET  ?q= -> accounts owning cloud resources, with authoritative earnings/executions.
//   POST {accountId, action: 'suspend'|'unsuspend', reason}
//
// "Suspend a developer" = take every PUBLISHED app + function and every ACTIVE agent they own
// off the air (status SUSPENDED with the reason), in one audited transaction. Unsuspend
// restores exactly the rows this suspension took down (matched by the recorded reason), so a
// DRAFT stays a DRAFT. No ledger balance is ever touched — earned money stays earned.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const q = url.searchParams.get('q')?.trim() ?? '';
    const { take, skip } = pageParams(req);

    const where: Record<string, unknown> = {
      OR: [{ cloudFunctions: { some: {} } }, { cloudApps: { some: {} } }, { cloudAgents: { some: {} } }],
    };
    if (q) {
      where.AND = [
        {
          OR: [
            { address: q },
            { handle: { contains: q, mode: 'insensitive' } },
            { displayName: { contains: q, mode: 'insensitive' } },
          ],
        },
      ];
    }

    const [rows, total] = await Promise.all([
      prisma.account.findMany({
        where: where as any,
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        select: {
          id: true,
          address: true,
          handle: true,
          displayName: true,
          createdAt: true,
          role: true,
          foundingDev: { select: { status: true, seq: true, feeBps: true, feeUntil: true } },
          _count: { select: { cloudFunctions: true, cloudApps: true, cloudAgents: true } },
        },
      }),
      prisma.account.count({ where: where as any }),
    ]);

    const ids = rows.map((r) => r.id);
    const [earnings, suspendedFns] = ids.length
      ? await Promise.all([
          prisma.cloudExecution.groupBy({
            by: ['developerAccountId'],
            where: { developerAccountId: { in: ids } },
            _count: { _all: true },
            _sum: { developerNanm: true },
          }),
          prisma.cloudFunction.groupBy({
            by: ['ownerId'],
            where: { ownerId: { in: ids }, status: 'SUSPENDED' },
            _count: { _all: true },
          }),
        ])
      : [[], []];
    const earnBy = new Map(earnings.map((r) => [r.developerAccountId, r]));
    const suspBy = new Map(suspendedFns.map((r) => [r.ownerId, r._count._all]));

    return ok({
      rows: rows.map((r) => ({
        ...r,
        live: {
          executions: earnBy.get(r.id)?._count._all ?? 0,
          earnedNanm: earnBy.get(r.id)?._sum.developerNanm ?? 0n,
          suspendedFunctions: suspBy.get(r.id) ?? 0,
        },
      })),
      total,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    const accountId = requireString(body, 'accountId');
    const action = requireString(body, 'action', 40);
    const reason = optionalString(body, 'reason');

    const account = await prisma.account.findUnique({
      where: { id: accountId },
      select: { id: true, address: true, handle: true },
    });
    if (!account) throw new ApiError(404, 'not_found', 'account not found');

    if (action === 'suspend') {
      if (!reason) throw new ApiError(400, 'bad_request', "'reason' is required to suspend a developer");
      const marker = `developer-suspension:${accountId}:${reason}`.slice(0, 500);
      const result = await prisma.$transaction(async (tx) => {
        const now = new Date();
        const apps = await tx.cloudApp.updateMany({
          where: { ownerId: accountId, status: 'PUBLISHED' },
          data: { status: 'SUSPENDED', suspendedAt: now, suspendedReason: marker },
        });
        const fns = await tx.cloudFunction.updateMany({
          where: { ownerId: accountId, status: 'PUBLISHED' },
          data: { status: 'SUSPENDED', suspendedAt: now, suspendedReason: marker },
        });
        const agents = await tx.cloudAgent.updateMany({
          where: { ownerId: accountId, status: 'ACTIVE' },
          data: { status: 'SUSPENDED' },
        });
        await audit(
          tx,
          actor,
          'developer.suspend',
          `account:${accountId}`,
          { address: account.address, suspended: false },
          { suspended: true, appsSuspended: apps.count, functionsSuspended: fns.count, agentsSuspended: agents.count, marker },
          reason,
        );
        return { apps: apps.count, functions: fns.count, agents: agents.count };
      });
      return ok({ suspended: result });
    }

    if (action === 'unsuspend') {
      const markerPrefix = `developer-suspension:${accountId}:`;
      const result = await prisma.$transaction(async (tx) => {
        const apps = await tx.cloudApp.updateMany({
          where: { ownerId: accountId, status: 'SUSPENDED', suspendedReason: { startsWith: markerPrefix } },
          data: { status: 'PUBLISHED', suspendedAt: null, suspendedReason: null },
        });
        const fns = await tx.cloudFunction.updateMany({
          where: { ownerId: accountId, status: 'SUSPENDED', suspendedReason: { startsWith: markerPrefix } },
          data: { status: 'PUBLISHED', suspendedAt: null, suspendedReason: null },
        });
        const agents = await tx.cloudAgent.updateMany({
          where: { ownerId: accountId, status: 'SUSPENDED' },
          data: { status: 'PAUSED' }, // owner re-activates explicitly; never auto-resume autonomy
        });
        await audit(
          tx,
          actor,
          'developer.unsuspend',
          `account:${accountId}`,
          { address: account.address, suspended: true },
          { suspended: false, appsRestored: apps.count, functionsRestored: fns.count, agentsPaused: agents.count },
          reason,
        );
        return { apps: apps.count, functions: fns.count, agents: agents.count };
      });
      return ok({ restored: result });
    }

    throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
  } catch (e) {
    return err(e);
  }
}
