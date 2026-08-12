import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/hosting -> the caller's content-hosting pins + accrued (IOU) hosting rewards.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');
    const pins = await prisma.hostingPin.findMany({
      where: { accountId: ctx.accountId },
      orderBy: { lastSeen: 'desc' },
      select: { cid: true, nodeId: true, bytes: true, byteHours: true, rewardNanm: true, active: true, pinnedAt: true, lastSeen: true },
    });
    const totalReward = pins.reduce((a, p) => a + p.rewardNanm, 0n);
    const totalBytes = pins.filter((p) => p.active).reduce((a, p) => a + p.bytes, 0);
    return ok({
      pins: jsonSafe(pins.map((p) => ({ ...p, rewardAnm: nanmToAnm(p.rewardNanm), byteHours: Math.round(p.byteHours) }))),
      activeReplicas: pins.filter((p) => p.active).length,
      bytesHostedActive: totalBytes,
      rewardAnmTotal: nanmToAnm(totalReward),
      settlement: 'IOU — treasury-funded hosting settlement is deferred (not yet a spendable balance)',
    });
  } catch (e) {
    return err(e);
  }
}
