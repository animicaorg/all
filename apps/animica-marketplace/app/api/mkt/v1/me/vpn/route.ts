import { NextRequest } from 'next/server';
import { authenticate, ok, err, ApiError } from '@/lib/api';
import { prisma } from '@/lib/db';
import { nanmToAnm, jsonSafe } from '@/lib/nanm';

export const dynamic = 'force-dynamic';

// GET /api/mkt/v1/me/vpn -> the caller's dVPN exits (with accrued IOU rewards) + their client
// sessions/usage. Rewards are IOUs only — honest accounting, same string contract as me/hosting.
export async function GET(req: NextRequest) {
  try {
    const ctx = await authenticate(req);
    if (!ctx) throw new ApiError(401, 'unauthorized', 'auth required');

    const exits = await prisma.vpnExit.findMany({
      where: { ownerId: ctx.accountId },
      orderBy: { lastSeenAt: 'desc' },
      // NOTE: proxyToken intentionally excluded.
      select: {
        id: true, label: true, region: true, country: true, city: true,
        wgPubkey: true, endpoint: true, httpProxy: true, capacityMbps: true,
        priceHintNanmPerGb: true, reputation: true, online: true, load: true,
        openSessions: true, rewardNanm: true, lastSeenAt: true, createdAt: true,
      },
    });
    const sessions = await prisma.vpnSession.findMany({
      where: { clientAccountId: ctx.accountId },
      orderBy: { startedAt: 'desc' },
      take: 200,
      select: {
        id: true, exitId: true, allocatedIp: true, status: true,
        clientCumBytes: true, exitCumBytes: true, reconciledBytes: true,
        rewardNanm: true, startedAt: true, endedAt: true, lastReportAt: true,
      },
    });

    const exitRewardTotal = exits.reduce((a, e) => a + e.rewardNanm, 0n);
    const sessionRewardTotal = sessions.reduce((a, s) => a + s.rewardNanm, 0n);

    return ok({
      exits: jsonSafe(exits.map((e) => ({
        ...e, hasHttpProxy: !!e.httpProxy, rewardAnm: nanmToAnm(e.rewardNanm),
      }))),
      sessions: jsonSafe(sessions.map((s) => ({
        ...s, rewardAnm: nanmToAnm(s.rewardNanm),
      }))),
      exitRewardAnmTotal: nanmToAnm(exitRewardTotal),
      sessionRewardAnmTotal: nanmToAnm(sessionRewardTotal),
      settlement: 'IOU — treasury-funded dVPN settlement is deferred (not yet a spendable balance)',
    });
  } catch (e) {
    return err(e);
  }
}
