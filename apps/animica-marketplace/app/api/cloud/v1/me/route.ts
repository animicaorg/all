import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { nanmToAnm } from '@/lib/nanm';
import { periodKeyFor } from '@/lib/planConfig';
import { limits as hardLimits } from '@/lib/cloud/config';
import {
  resolvePlan,
  getUsage,
  concurrencyFor,
  priorityFor,
  logRetentionDays,
  scheduleFloorMinutes,
  USAGE,
} from '@/lib/cloud/entitlements';

export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/me
//
// The caller's Python Cloud account snapshot: resolved plan + entitlements, LIVE usage
// against every limit (real COUNT/SUM queries and the durable UsageCounter rows — nothing
// cached, nothing invented), ledger balance and promotional credit runway.

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');
    const now = new Date();
    const dayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));

    const [account, plan] = await Promise.all([
      prisma.account.findUnique({
        where: { id: ctx.accountId },
        select: { id: true, address: true, handle: true, displayName: true, balanceNanm: true, createdAt: true },
      }),
      resolvePlan(ctx.accountId, now),
    ]);
    if (!account) throw new ApiError(404, 'not_found', 'account not found');

    const [
      execUsed,
      cpuUsed,
      aiUsed,
      deploysToday,
      functionsPublished,
      functionsTotal,
      appsCount,
      agentsCount,
      schedulesCount,
      secretsCount,
      runningNow,
      credits,
    ] = await Promise.all([
      getUsage(ctx.accountId, USAGE.executions, now),
      getUsage(ctx.accountId, USAGE.computeUnits, now),
      getUsage(ctx.accountId, USAGE.aiUnits, now),
      prisma.cloudDeployment.count({ where: { createdAt: { gte: dayStart }, function: { ownerId: ctx.accountId } } }),
      prisma.cloudFunction.count({ where: { ownerId: ctx.accountId, status: 'PUBLISHED' } }),
      prisma.cloudFunction.count({ where: { ownerId: ctx.accountId, status: { not: 'ARCHIVED' } } }),
      prisma.cloudApp.count({ where: { ownerId: ctx.accountId, status: { not: 'ARCHIVED' } } }),
      prisma.cloudAgent.count({ where: { ownerId: ctx.accountId } }),
      prisma.cloudSchedule.count({ where: { ownerId: ctx.accountId } }),
      prisma.cloudSecret.count({ where: { ownerId: ctx.accountId, NOT: { name: { startsWith: '__state__' } } } }),
      prisma.cloudExecution.count({
        where: { callerAccountId: ctx.accountId, status: { in: ['QUEUED', 'DISPATCHED', 'RUNNING'] } },
      }),
      prisma.cloudCredit.findMany({
        where: { accountId: ctx.accountId, revokedAt: null, OR: [{ expiresAt: null }, { expiresAt: { gt: now } }] },
        select: { grantedNanm: true, usedNanm: true, expiresAt: true, source: true },
      }),
    ]);

    const creditNanm = credits.reduce((a, c) => a + (c.grantedNanm - c.usedNanm), 0n);
    const L = plan.limits;
    const gauge = (used: number, limit: number) => ({ used, limit, unlimited: limit === -1 });

    return ok({
      account: {
        id: account.id,
        address: account.address,
        handle: account.handle,
        displayName: account.displayName,
        createdAt: account.createdAt,
      },
      balance: {
        balanceNanm: account.balanceNanm,
        balanceAnm: nanmToAnm(account.balanceNanm),
        creditNanm,
        creditAnm: nanmToAnm(creditNanm),
        credits: credits.map((c) => ({
          remainingNanm: c.grantedNanm - c.usedNanm,
          source: c.source,
          expiresAt: c.expiresAt,
        })),
      },
      plan: {
        key: plan.key,
        source: plan.source,
        blockNewPaidResources: plan.blockNewPaidResources,
        subscription: plan.subscription,
        founding: plan.founding,
      },
      entitlements: L,
      usage: {
        period: periodKeyFor(now),
        executions: gauge(execUsed, L.monthly_executions),
        computeUnits: gauge(cpuUsed, L.monthly_compute_units),
        aiUnits: gauge(aiUsed, L.monthly_ai_units),
        deploysToday: gauge(deploysToday, L.max_deployments_per_day),
        functionsPublished: gauge(functionsPublished, L.max_functions),
        functionsTotal,
        apps: gauge(appsCount, L.max_apps),
        agents: gauge(agentsCount, L.max_agents),
        schedules: gauge(schedulesCount, L.max_schedules),
        secrets: gauge(secretsCount, L.max_secrets),
        runningNow,
      },
      operational: {
        concurrency: concurrencyFor(plan),
        priorityClass: priorityFor(plan),
        logRetentionDays: logRetentionDays(plan),
        scheduleFloorMinutes: scheduleFloorMinutes(plan),
        maxTimeoutMs: hardLimits.maxTimeoutMs,
        maxMemoryMb: hardLimits.maxMemoryMb,
        maxSourceBytes: hardLimits.maxSourceBytes,
      },
    });
  } catch (e) {
    return err(e);
  }
}
