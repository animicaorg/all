import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';
import { isRangeKey, rangeFor } from '@/lib/cloud/finance';
import { pageParams } from '../_lib';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/admin/executions — the execution browser (§39) and the drill-down target
// for every execution-derived figure on /admin/profitability (§82).
//
// Filters (all optional):
//   range=today|24h|7d|30d|mtd|90d|all   status=SUCCEEDED|FAILED|...   lane=local|fleet
//   free=1 (free tier only)  priced=1 (priceNanm>0)  ai=1 (AI-consuming)
//   negative=1 (contribution < 0 — the workloads that LOSE money, §90)
//   q= requestId | execution id       account= caller/developer account id
//   functionId=  appId=  agentId=  providerId=

export async function GET(req: NextRequest) {
  try {
    await requireAdmin(req);
    const url = new URL(req.url);
    const p = url.searchParams;
    const { take, skip } = pageParams(req);

    const where: Record<string, any> = {};
    const rangeParam = p.get('range') ?? 'all';
    if (!isRangeKey(rangeParam)) throw new ApiError(400, 'bad_request', 'invalid range');
    const range = rangeFor(rangeParam);
    if (range.start) where.createdAt = { gte: range.start, lt: range.end };

    const status = p.get('status');
    if (status) where.status = status;
    const lane = p.get('lane');
    if (lane) where.lane = lane;
    if (p.get('free') === '1') where.freeTier = true;
    if (p.get('priced') === '1') where.priceNanm = { gt: 0n };
    if (p.get('negative') === '1') {
      where.contributionNanm = { lt: 0n };
      where.priceNanm = { gt: 0n };
    }
    if (p.get('ai') === '1') {
      where.OR = [{ aiCalls: { gt: 0 } }, { aiTokensIn: { gt: 0 } }, { aiTokensOut: { gt: 0 } }];
    }
    const q = p.get('q')?.trim();
    if (q) where.AND = [{ OR: [{ requestId: q }, { id: q }] }];
    const account = p.get('account')?.trim();
    if (account) {
      where.AND = [...(where.AND ?? []), { OR: [{ callerAccountId: account }, { developerAccountId: account }] }];
    }
    for (const f of ['functionId', 'appId', 'agentId', 'providerId'] as const) {
      const v = p.get(f);
      if (v) where[f] = v;
    }

    const [rows, total, agg] = await Promise.all([
      prisma.cloudExecution.findMany({
        where,
        orderBy: { createdAt: 'desc' },
        take,
        skip,
        include: {
          function: { select: { slug: true, name: true } },
          app: { select: { slug: true, name: true } },
          agent: { select: { slug: true, name: true } },
          developer: { select: { address: true, handle: true } },
          caller: { select: { address: true, handle: true } },
          provider: { select: { name: true, address: true } },
        },
      }),
      prisma.cloudExecution.count({ where }),
      prisma.cloudExecution.aggregate({
        where,
        _sum: { priceNanm: true, platformFeeNanm: true, cogsNanm: true, contributionNanm: true },
      }),
    ]);

    return ok({
      rows,
      total,
      totals: {
        grossNanm: agg._sum.priceNanm ?? 0n,
        platformFeeNanm: agg._sum.platformFeeNanm ?? 0n,
        cogsNanm: agg._sum.cogsNanm ?? 0n,
        contributionNanm: agg._sum.contributionNanm ?? 0n,
      },
    });
  } catch (e) {
    return err(e);
  }
}
