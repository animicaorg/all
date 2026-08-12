import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { sandboxLoad, reapOrphans } from '@/lib/cloud/sandbox';
import { limits } from '@/lib/cloud/config';
import { adminActor, audit, readJson, optionalString } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// /api/cloud/v1/admin/overview — operational system health (§39).
//   GET  -> sandbox load, queue depths, provider fleet state, failure/report/alert counts,
//           recent audit trail. Every number is a live query.
//   POST {action:'reap_orphans'} -> kill orphaned sandbox containers older than the TTL.

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const now = Date.now();
    const staleCutoff = new Date(now - limits.providerStaleSeconds * 1000);
    const dayAgo = new Date(now - 86_400_000);
    const weekAgo = new Date(now - 7 * 86_400_000);

    const [
      execQueued,
      execRunning,
      exec24h,
      execFailed24h,
      jobsPending,
      jobsClaimed,
      providersActive,
      providersStale,
      providersSuspended,
      deployFailed7d,
      reportsOpen,
      alertsOpen,
      denylistCount,
      appsPublished,
      appsSuspended,
      functionsPublished,
      agentsActive,
      auditRecent,
    ] = await Promise.all([
      prisma.cloudExecution.count({ where: { status: { in: ['QUEUED', 'DISPATCHED'] } } }),
      prisma.cloudExecution.count({ where: { status: 'RUNNING' } }),
      prisma.cloudExecution.count({ where: { createdAt: { gte: dayAgo } } }),
      prisma.cloudExecution.count({ where: { createdAt: { gte: dayAgo }, status: { in: ['FAILED', 'TIMEOUT', 'REJECTED'] } } }),
      prisma.cloudJob.count({ where: { status: 'PENDING' } }),
      prisma.cloudJob.count({ where: { status: { in: ['CLAIMED', 'RUNNING'] } } }),
      prisma.cloudProvider.count({ where: { status: 'ACTIVE', lastSeenAt: { gte: staleCutoff } } }),
      prisma.cloudProvider.count({ where: { status: 'ACTIVE', lastSeenAt: { lt: staleCutoff } } }),
      prisma.cloudProvider.count({ where: { status: { in: ['SUSPENDED', 'DISABLED'] } } }),
      prisma.cloudDeployment.count({ where: { status: 'FAILED', createdAt: { gte: weekAgo } } }),
      prisma.cloudReport.count({ where: { status: { in: ['OPEN', 'REVIEWING'] } } }),
      prisma.financeAlert.count({ where: { resolvedAt: null } }),
      prisma.cloudCodeDenylist.count(),
      prisma.cloudApp.count({ where: { status: 'PUBLISHED' } }),
      prisma.cloudApp.count({ where: { status: 'SUSPENDED' } }),
      prisma.cloudFunction.count({ where: { status: 'PUBLISHED' } }),
      prisma.cloudAgent.count({ where: { status: 'ACTIVE' } }),
      prisma.cloudAuditLog.findMany({ orderBy: { createdAt: 'desc' }, take: 25 }),
    ]);

    return ok({
      sandbox: { ...sandboxLoad(), queueMaxDepth: limits.queueMaxDepth },
      executions: { queued: execQueued, running: execRunning, last24h: exec24h, failed24h: execFailed24h },
      fleet: {
        jobsPending,
        jobsInFlight: jobsClaimed,
        providersActive,
        providersStale,
        providersSuspended,
        staleAfterSeconds: limits.providerStaleSeconds,
      },
      catalog: { appsPublished, appsSuspended, functionsPublished, agentsActive },
      attention: { deployFailed7d, reportsOpen, alertsOpen, denylistCount },
      audit: auditRecent,
    });
  } catch (e) {
    return err(e);
  }
}

export async function POST(req: NextRequest) {
  try {
    const actor = await adminActor(req);
    const body = await readJson(req);
    if (body.action === 'reap_orphans') {
      const killed = await reapOrphans();
      await audit(
        prisma,
        actor,
        'sandbox.reap_orphans',
        'sandbox',
        {},
        { containersKilled: killed },
        optionalString(body, 'reason') || 'manual orphan sweep',
      );
      return ok({ killed });
    }
    throw new ApiError(400, 'bad_request', `unknown action '${String(body.action)}'`);
  } catch (e) {
    return err(e);
  }
}
