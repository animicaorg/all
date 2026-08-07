import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';

export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/me/analytics?days=30
//
// Real aggregates over CloudExecution — nothing precomputed, nothing invented:
//   asDeveloper: traffic to the caller's functions (executions, success rate, p50/p95
//                duration over finished runs, unique callers, ANM revenue, compute + AI
//                consumption, top functions).
//   asCaller:    the caller's own consumption (spend, compute, AI tokens).
// Internal cost accounting (COGS/margin) is deliberately absent — that is /admin territory (§74).

interface DevAggRow {
  executions: bigint;
  succeeded: bigint;
  failed: bigint;
  unique_callers: bigint;
  anonymous_calls: bigint;
  developer_nanm: bigint;
  gross_nanm: bigint;
  cpu_ms: bigint;
  memory_mb_ms: bigint;
  ai_tokens_in: bigint;
  ai_tokens_out: bigint;
  ai_calls: bigint;
  p50_ms: number;
  p95_ms: number;
}

export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'read');

    const daysRaw = Number(req.nextUrl.searchParams.get('days') ?? 30);
    const days = Number.isFinite(daysRaw) ? Math.min(Math.max(Math.trunc(daysRaw), 1), 90) : 30;
    const since = new Date(Date.now() - days * 86_400_000);
    const me = ctx.accountId;

    const [devRows, topFunctions, callerAgg] = await Promise.all([
      prisma.$queryRaw<DevAggRow[]>`
        SELECT COUNT(*)::bigint                                                          AS executions,
               COUNT(*) FILTER (WHERE "status" = 'SUCCEEDED')::bigint                    AS succeeded,
               COUNT(*) FILTER (WHERE "status" IN ('FAILED', 'TIMEOUT'))::bigint         AS failed,
               COUNT(DISTINCT "callerAccountId")::bigint                                 AS unique_callers,
               COUNT(*) FILTER (WHERE "callerAccountId" IS NULL)::bigint                 AS anonymous_calls,
               COALESCE(SUM("developerNanm"), 0)::bigint                                 AS developer_nanm,
               COALESCE(SUM("priceNanm"), 0)::bigint                                     AS gross_nanm,
               COALESCE(SUM("cpuMs"), 0)::bigint                                         AS cpu_ms,
               COALESCE(SUM("memoryMbMs"), 0)::bigint                                    AS memory_mb_ms,
               COALESCE(SUM("aiTokensIn"), 0)::bigint                                    AS ai_tokens_in,
               COALESCE(SUM("aiTokensOut"), 0)::bigint                                   AS ai_tokens_out,
               COALESCE(SUM("aiCalls"), 0)::bigint                                       AS ai_calls,
               COALESCE(percentile_cont(0.5)  WITHIN GROUP (ORDER BY "durationMs")
                          FILTER (WHERE "finishedAt" IS NOT NULL), 0)::float8            AS p50_ms,
               COALESCE(percentile_cont(0.95) WITHIN GROUP (ORDER BY "durationMs")
                          FILTER (WHERE "finishedAt" IS NOT NULL), 0)::float8            AS p95_ms
        FROM "CloudExecution"
        WHERE "developerAccountId" = ${me} AND "createdAt" >= ${since}`,
      prisma.cloudExecution.groupBy({
        by: ['functionId'],
        where: { developerAccountId: me, createdAt: { gte: since } },
        _count: { _all: true },
        _sum: { developerNanm: true, priceNanm: true, cpuMs: true, aiTokensIn: true, aiTokensOut: true },
        orderBy: { _count: { functionId: 'desc' } },
        take: 10,
      }),
      prisma.cloudExecution.aggregate({
        where: { callerAccountId: me, createdAt: { gte: since } },
        _count: { _all: true },
        _sum: { priceNanm: true, creditNanm: true, cpuMs: true, aiTokensIn: true, aiTokensOut: true },
      }),
    ]);

    const dev = devRows[0];
    const executions = Number(dev?.executions ?? 0n);
    const succeeded = Number(dev?.succeeded ?? 0n);

    // Success rate over FINISHED runs (succeeded + failed/timeout); in-flight rows are neither.
    const finished = succeeded + Number(dev?.failed ?? 0n);
    const successRate = finished > 0 ? succeeded / finished : null;

    const fnIds = topFunctions.map((r) => r.functionId);
    const fns = fnIds.length
      ? await prisma.cloudFunction.findMany({
          where: { id: { in: fnIds } },
          select: { id: true, slug: true, name: true, status: true },
        })
      : [];
    const fnById = new Map(fns.map((f) => [f.id, f]));

    return ok({
      windowDays: days,
      since,
      asDeveloper: {
        executions,
        succeeded,
        failed: dev?.failed ?? 0n,
        successRate,
        p50DurationMs: Math.round(dev?.p50_ms ?? 0),
        p95DurationMs: Math.round(dev?.p95_ms ?? 0),
        uniqueCallers: dev?.unique_callers ?? 0n,
        anonymousCalls: dev?.anonymous_calls ?? 0n,
        revenue: {
          developerNanm: dev?.developer_nanm ?? 0n,
          grossNanm: dev?.gross_nanm ?? 0n,
        },
        consumption: {
          cpuMs: dev?.cpu_ms ?? 0n,
          memoryMbMs: dev?.memory_mb_ms ?? 0n,
          aiTokensIn: dev?.ai_tokens_in ?? 0n,
          aiTokensOut: dev?.ai_tokens_out ?? 0n,
          aiCalls: dev?.ai_calls ?? 0n,
        },
        topFunctions: topFunctions.map((r) => ({
          functionId: r.functionId,
          slug: fnById.get(r.functionId)?.slug ?? null,
          name: fnById.get(r.functionId)?.name ?? null,
          status: fnById.get(r.functionId)?.status ?? null,
          executions: r._count._all,
          developerNanm: r._sum.developerNanm ?? 0n,
          grossNanm: r._sum.priceNanm ?? 0n,
          cpuMs: r._sum.cpuMs ?? 0,
          aiTokens: (r._sum.aiTokensIn ?? 0) + (r._sum.aiTokensOut ?? 0),
        })),
      },
      asCaller: {
        executions: callerAgg._count._all,
        spentNanm: callerAgg._sum.priceNanm ?? 0n,
        creditNanm: callerAgg._sum.creditNanm ?? 0n,
        cpuMs: callerAgg._sum.cpuMs ?? 0,
        aiTokensIn: callerAgg._sum.aiTokensIn ?? 0,
        aiTokensOut: callerAgg._sum.aiTokensOut ?? 0,
      },
    });
  } catch (e) {
    return err(e);
  }
}
