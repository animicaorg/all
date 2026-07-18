import { NextRequest } from 'next/server';
import { authenticate, requireScope, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { isCid } from '@/lib/storage';
import { heartbeatPin, reapStalePins } from '@/lib/hosting';
import { nanmToAnm } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// POST /api/mkt/v1/content/[cid]/pin { nodeId }  (scope: host)
// A node announces/heartbeats that it hosts this CID. Rolls its hosting-reward accrual forward and
// (if the CID backs a .anm domain) adds the node to that domain's providers. Rewards are IOUs.
export async function POST(req: NextRequest, { params }: { params: { cid: string } }) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    requireScope(ctx, 'host');
    const cid = params.cid;
    if (!isCid(cid)) throw new ApiError(400, 'bad_cid', 'not a valid anm1c CID');
    const obj = await prisma.contentObject.findUnique({ where: { cid }, select: { size: true } });
    if (!obj) throw new ApiError(404, 'not_found', 'no object for CID');

    const body = await req.json().catch(() => ({}));
    const nodeId = String(body.nodeId || ctx.accountId).slice(0, 128);
    if (!nodeId) throw new ApiError(400, 'bad_node', 'nodeId required');

    const res = await heartbeatPin({ cid, nodeId, accountId: ctx.accountId, bytes: obj.size });

    // Reflect the provider on any .anm domains pointing at this CID.
    const domains = await prisma.anmDomain.findMany({ where: { contentCid: cid }, select: { name: true, nodeProviders: true } });
    for (const d of domains) {
      const providers = Array.from(new Set([...(d.nodeProviders ?? []), nodeId])).slice(0, 32);
      await prisma.anmDomain.update({ where: { name: d.name }, data: { nodeProviders: providers } }).catch(() => {});
    }
    reapStalePins().catch(() => {});

    return ok({
      pinned: true, cid, nodeId,
      byteHours: Math.round(res.byteHours),
      rewardAnm: nanmToAnm(res.rewardNanm),
      replicas: res.totalPins,
      note: 'hosting reward is an IOU (treasury-funded settlement is deferred; not a spendable credit yet)',
    });
  } catch (e) {
    return err(e);
  }
}
