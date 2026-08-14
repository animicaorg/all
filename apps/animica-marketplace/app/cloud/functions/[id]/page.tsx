import { notFound } from 'next/navigation';
import { prisma } from '@/lib/db';
import { jsonSafe } from '@/lib/nanm';
import { runtime } from '@/lib/cloud/config';
import { cloudSession, cloudAccount, ownerSegment } from '@/components/cloud/server';
import CloudGate from '@/components/cloud/CloudGate';
import FunctionDetailClient, { type FunctionDetailDto } from './FunctionDetailClient';

export const dynamic = 'force-dynamic';

// /cloud/functions/[id] — versions + diff/rollback, deployments with their on-chain anchor
// (or the honest recorded reason there is none), executions, live logs, settings, danger zone.
export default async function FunctionDetailPage({ params }: { params: { id: string } }) {
  const sess = cloudSession();
  if (!sess) return <CloudGate />;
  const me = sess.accountId;

  const fn = await prisma.cloudFunction.findFirst({
    where: { id: params.id, ownerId: me },
    include: { app: { select: { id: true, slug: true, name: true } } },
  });
  if (!fn) return notFound();

  const [account, versions, deployments, executions, logs, stats, stats30, secrets, schedules] = await Promise.all([
    cloudAccount(me),
    prisma.cloudFunctionVersion.findMany({
      where: { functionId: fn.id },
      orderBy: { version: 'desc' },
      take: 12,
      select: {
        id: true, version: true, source: true, sourceSha3: true, artifactSha3: true,
        sizeBytes: true, entrypoint: true, packages: true, estimateNanm: true, createdAt: true,
      },
    }),
    prisma.cloudDeployment.findMany({
      where: { functionId: fn.id },
      orderBy: { createdAt: 'desc' },
      take: 15,
      include: { version: { select: { version: true } } },
    }),
    prisma.cloudExecution.findMany({
      where: { functionId: fn.id },
      orderBy: { createdAt: 'desc' },
      take: 30,
      select: {
        id: true, requestId: true, status: true, errorCode: true, durationMs: true, cpuMs: true,
        priceNanm: true, developerNanm: true, freeTier: true, callerKind: true, lane: true, createdAt: true,
      },
    }),
    prisma.cloudExecutionLog.findMany({
      where: { execution: { functionId: fn.id } },
      orderBy: { ts: 'desc' },
      take: 60,
      select: { id: true, ts: true, level: true, message: true, executionId: true },
    }),
    prisma.cloudExecution.aggregate({
      where: { functionId: fn.id },
      _count: { _all: true },
      _sum: { developerNanm: true, priceNanm: true },
    }),
    prisma.cloudExecution.groupBy({
      by: ['status'],
      where: { functionId: fn.id, createdAt: { gte: new Date(Date.now() - 30 * 86_400_000) } },
      _count: { _all: true },
    }),
    prisma.cloudSecret.findMany({
      where: { ownerId: me, functionId: fn.id },
      select: { id: true, name: true, hint: true, createdAt: true },
    }),
    prisma.cloudSchedule.findMany({
      where: { functionId: fn.id },
      select: { id: true, kind: true, intervalMinutes: true, cron: true, enabled: true, nextRunAt: true, lastRunAt: true, lastStatus: true, runsTotal: true },
    }),
  ]);
  if (!account) return <CloudGate />;

  const succeeded30 = stats30.find((s) => s.status === 'SUCCEEDED')?._count._all ?? 0;
  const failed30 = stats30
    .filter((s) => s.status === 'FAILED' || s.status === 'TIMEOUT')
    .reduce((a, s) => a + s._count._all, 0);

  const dto: FunctionDetailDto = jsonSafe({
    fn: {
      id: fn.id,
      slug: fn.slug,
      name: fn.name,
      description: fn.description,
      status: fn.status,
      visibility: fn.visibility,
      entrypoint: fn.entrypoint,
      runtime: fn.runtime,
      timeoutMs: fn.timeoutMs,
      memoryMb: fn.memoryMb,
      capabilities: fn.capabilities,
      requiresAuth: fn.requiresAuth,
      perCallNanm: fn.perCallNanm,
      currentVersion: fn.currentVersion,
      suspendedAt: fn.suspendedAt,
      suspendedReason: fn.suspendedReason,
      createdAt: fn.createdAt,
      app: fn.app,
    },
    ownerSegment: ownerSegment(account),
    publicBase: runtime.publicBase,
    anchorConfirmations: runtime.anchorConfirmations,
    versions,
    deployments: deployments.map((d) => ({
      id: d.id,
      status: d.status,
      version: d.version.version,
      daBlobId: d.daBlobId,
      anchorTxid: d.anchorTxid,
      anchorHeight: d.anchorHeight,
      anchorConfirms: d.anchorConfirms,
      registryName: d.registryName,
      deployerAddress: d.deployerAddress,
      endpoint: d.endpoint,
      error: d.error,
      logsJson: d.logsJson,
      createdAt: d.createdAt,
      activatedAt: d.activatedAt,
    })),
    executions,
    logs,
    stats: {
      execTotal: stats._count._all,
      netNanm: stats._sum.developerNanm ?? 0n,
      grossNanm: stats._sum.priceNanm ?? 0n,
      succeeded30,
      failed30,
    },
    secrets,
    schedules,
  }) as unknown as FunctionDetailDto;

  return <FunctionDetailClient dto={dto} />;
}
