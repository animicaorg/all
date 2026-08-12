import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { limits } from '@/lib/cloud/config';
import { adminActor, audit, readJson, requireString, optionalString, pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/providers — the compute fleet (§39).
//   GET  -> providers with authoritative earnings (SUM of CloudExecution.providerNanm, not the cache).
//   POST {providerId, action: 'suspend'|'reactivate'|'disable', reason} — availability, audited.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const status = url.searchParams.get('status') ?? '';
    const { take, skip } = pageParams(req);
    const where = status ? { status: status as any } : {};
    const [rows, total] = await Promise.all([
      prisma.cloudProvider.findMany({
        where,
        orderBy: { lastSeenAt: 'desc' },
        take,
        skip,
        include: { account: { select: { address: true, handle: true } }, _count: { select: { jobs: true } } },
      }),
      prisma.cloudProvider.count({ where }),
    ]);
    const ids = rows.map((r) => r.id);
    const earned = ids.length
      ? await prisma.cloudExecution.groupBy({
          by: ['providerId'],
          where: { providerId: { in: ids } },
          _sum: { providerNanm: true },
          _count: { _all: true },
        })
      : [];
    const earnedBy = new Map(earned.map((r) => [r.providerId, r]));
    const staleCutoff = Date.now() - limits.providerStaleSeconds * 1000;
    return ok({
      rows: rows.map((r) => ({
        ...r,
        stale: r.lastSeenAt.getTime() < staleCutoff,
        live: {
          executions: earnedBy.get(r.id)?._count._all ?? 0,
          earnedNanm: earnedBy.get(r.id)?._sum.providerNanm ?? 0n,
          cacheEarnedNanm: r.earnedNanm, // shown next to the recomputed truth
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
    const providerId = requireString(body, 'providerId');
    const action = requireString(body, 'action', 40);
    const reason = optionalString(body, 'reason');

    const provider = await prisma.cloudProvider.findUnique({ where: { id: providerId } });
    if (!provider) throw new ApiError(404, 'not_found', 'provider not found');

    const transitions: Record<string, { to: 'SUSPENDED' | 'DISABLED' | 'ACTIVE'; needsReason: boolean }> = {
      suspend: { to: 'SUSPENDED', needsReason: true },
      disable: { to: 'DISABLED', needsReason: true },
      reactivate: { to: 'ACTIVE', needsReason: false },
    };
    const t = transitions[action];
    if (!t) throw new ApiError(400, 'bad_request', `unknown action '${action}'`);
    if (t.needsReason && !reason) throw new ApiError(400, 'bad_request', `'reason' is required to ${action} a provider`);
    if (provider.status === t.to) throw new ApiError(409, 'conflict', `provider is already ${t.to}`);

    const updated = await prisma.$transaction(async (tx) => {
      const row = await tx.cloudProvider.update({
        where: { id: providerId },
        data: { status: t.to, suspendedReason: t.to === 'ACTIVE' ? null : reason },
      });
      // Unclaim this provider's leased-but-unfinished jobs so other providers can take them.
      let jobsReleased = 0;
      if (t.to !== 'ACTIVE') {
        const released = await tx.cloudJob.updateMany({
          where: { providerId, status: { in: ['CLAIMED', 'RUNNING'] } },
          data: { status: 'PENDING', providerId: null, leaseUntil: null },
        });
        jobsReleased = released.count;
      }
      await audit(
        tx,
        actor,
        `provider.${action}`,
        `cloud_provider:${providerId}`,
        { status: provider.status, name: provider.name, address: provider.address },
        { status: t.to, jobsReleased },
        reason,
      );
      return row;
    });
    return ok({ provider: updated });
  } catch (e) {
    return err(e);
  }
}
