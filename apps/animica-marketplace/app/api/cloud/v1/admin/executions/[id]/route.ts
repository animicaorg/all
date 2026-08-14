import { NextRequest } from 'next/server';
import { prisma } from '@/lib/db';
import { ok, err, ApiError } from '@/lib/api';
import { requireAdmin } from '@/lib/adminAuth';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

// GET /api/cloud/v1/admin/executions/:id — the FULL execution record (§39):
//   * the execution row with function/app/agent/caller/developer/provider context,
//   * the complete nested-call trace (every execution sharing the rootId, depth-ordered),
//   * the structured logs,
//   * the fleet job (when dispatched), and
//   * the LEDGER ENTRIES the settlement posted (ref = execution id) — money and record
//     side-by-side, exactly what an operator needs to trust (or distrust) a charge.
export async function GET(req: NextRequest, { params }: { params: { id: string } }) {
  try {
    await requireAdmin(req);
    const id = params.id;
    const row = await prisma.cloudExecution.findFirst({
      where: { OR: [{ id }, { requestId: id }] },
      include: {
        function: { select: { id: true, slug: true, name: true, ownerId: true, entrypoint: true } },
        version: { select: { id: true, version: true, sourceSha3: true, artifactSha3: true, sizeBytes: true } },
        app: { select: { id: true, slug: true, name: true, status: true } },
        agent: { select: { id: true, slug: true, name: true, status: true } },
        developer: { select: { id: true, address: true, handle: true, displayName: true } },
        caller: { select: { id: true, address: true, handle: true, displayName: true } },
        provider: { select: { id: true, name: true, address: true, status: true } },
        job: true,
        logs: { orderBy: { seq: 'asc' }, take: 500 },
      },
    });
    if (!row) throw new ApiError(404, 'not_found', 'execution not found');

    const rootId = row.rootId ?? row.id;
    const [trace, ledger] = await Promise.all([
      prisma.cloudExecution.findMany({
        where: { OR: [{ rootId }, { id: rootId }] },
        orderBy: [{ depth: 'asc' }, { queuedAt: 'asc' }],
        select: {
          id: true,
          requestId: true,
          parentExecutionId: true,
          depth: true,
          status: true,
          lane: true,
          durationMs: true,
          priceNanm: true,
          platformFeeNanm: true,
          developerNanm: true,
          providerNanm: true,
          cogsNanm: true,
          contributionNanm: true,
          freeTier: true,
          errorCode: true,
          createdAt: true,
          function: { select: { slug: true, name: true } },
        },
      }),
      // Every ledger entry posted for any execution in this trace (settlement uses ref=executionId).
      prisma.ledgerEntry.findMany({
        where: { ref: { in: [rootId, row.id] } },
        orderBy: { createdAt: 'asc' },
        include: { account: { select: { address: true, displayName: true, handle: true } } },
      }),
    ]);

    // Extend the ledger view to the whole trace when it has more executions.
    const traceIds = trace.map((t) => t.id).filter((tid) => tid !== rootId && tid !== row.id);
    const moreLedger = traceIds.length
      ? await prisma.ledgerEntry.findMany({
          where: { ref: { in: traceIds } },
          orderBy: { createdAt: 'asc' },
          include: { account: { select: { address: true, displayName: true, handle: true } } },
        })
      : [];
    const allLedger = [...ledger, ...moreLedger];
    const ledgerNet = allLedger.reduce((a, e) => a + e.deltaNanm, 0n);

    return ok({
      execution: row,
      trace,
      ledger: allLedger,
      ledgerNetNanm: ledgerNet, // must be 0 for a settled trace — shown, not assumed
      splitExact: row.priceNanm === row.platformFeeNanm + row.developerNanm + row.providerNanm,
    });
  } catch (e) {
    return err(e);
  }
}
