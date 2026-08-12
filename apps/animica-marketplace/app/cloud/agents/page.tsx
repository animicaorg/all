import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { resolvePlan } from '@/lib/cloud/entitlements';
import { cloudSession, cloudAccount, ownerSegment } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import AgentsClient, { type AgentsDto } from './AgentsClient';

export const dynamic = 'force-dynamic';

// /cloud/agents — persistent, capability-bounded programs with their own budget.
export default async function CloudAgentsPage() {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const [account, plan, agents, functions] = await Promise.all([
    cloudAccount(me),
    resolvePlan(me),
    prisma.cloudAgent.findMany({
      where: { ownerId: me },
      orderBy: { createdAt: 'desc' },
      include: { function: { select: { id: true, slug: true, name: true, status: true } } },
    }),
    prisma.cloudFunction.findMany({
      where: { ownerId: me },
      orderBy: { updatedAt: 'desc' },
      select: { id: true, slug: true, name: true, status: true },
    }),
  ]);
  if (!account) return <CloudGate />;

  const [grantCounts, runCounts] = await Promise.all([
    agents.length
      ? prisma.cloudGrant.groupBy({
          by: ['subjectId'],
          where: { subjectKind: 'agent', subjectId: { in: agents.map((a) => a.id) }, revokedAt: null },
          _count: { _all: true },
        })
      : Promise.resolve([] as { subjectId: string; _count: { _all: number } }[]),
    agents.length
      ? prisma.cloudExecution.groupBy({
          by: ['agentId'],
          where: { agentId: { in: agents.map((a) => a.id) } },
          _count: { _all: true },
          _sum: { priceNanm: true },
        })
      : Promise.resolve([] as any[]),
  ]);
  const grantsByAgent = new Map(grantCounts.map((g) => [g.subjectId, g._count._all]));
  const runsByAgent = new Map(runCounts.map((r: any) => [r.agentId, { count: r._count._all, spent: r._sum.priceNanm ?? 0n }]));

  const dto: AgentsDto = jsonSafe({
    ownerSegment: ownerSegment(account),
    plan: { key: plan.key, maxAgents: plan.limits.max_agents, used: agents.length },
    functions,
    agents: agents.map((a) => ({
      id: a.id,
      slug: a.slug,
      name: a.name,
      description: a.description,
      status: a.status,
      address: a.address,
      capabilities: a.capabilities,
      maxSpendPerRunNanm: a.maxSpendPerRunNanm,
      dailySpendCapNanm: a.dailySpendCapNanm,
      spentTodayNanm: a.spentTodayNanm,
      spendDayKey: a.spendDayKey,
      lastRunAt: a.lastRunAt,
      runsTotal: a.runsTotal,
      createdAt: a.createdAt,
      function: a.function,
      grants: grantsByAgent.get(a.id) ?? 0,
      execCount: runsByAgent.get(a.id)?.count ?? 0,
      execSpendNanm: runsByAgent.get(a.id)?.spent ?? 0n,
    })),
  }) as unknown as AgentsDto;

  return <AgentsClient dto={dto} />;
}
