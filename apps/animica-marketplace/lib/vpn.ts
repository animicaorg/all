import { prisma } from './db';
import {
  VPN_NANM_PER_BYTE,
  VPN_STALE_SECONDS,
  VPN_DIVERGENCE_BPS,
  VPN_SESSION_MAX_NANM,
  VPN_NODE_DAILY_MAX_NANM,
  VPN_GLOBAL_DAILY_MAX_NANM,
  VPN_MAX_CAPACITY_MBPS,
} from './config';

// dVPN relay-reward accrual for WireGuard exits. Reward = reconciled bytes × a fixed per-byte rate,
// where "reconciled" is the min of the two peers' signed cumulative counters (client + exit), each
// capped at the exit's physical capacity over the session wall-clock. IMPORTANT: this accrues an IOU
// (VpnExit.rewardNanm / VpnSession.rewardNanm) — it is NOT credited to the spendable ledger. Real
// settlement is treasury-funded and deferred, exactly like hosting/AICF pay. Never present it as an
// executed transfer. Mirrors lib/hosting.ts.

export function vpnRewardNanm(reconciledBytes: number): bigint {
  if (!(reconciledBytes > 0)) return 0n;
  return BigInt(Math.floor(reconciledBytes * VPN_NANM_PER_BYTE));
}

// Pure reconciliation: reward the byte count both sides agree on (min), never above the physical
// capacity ceiling (capacityBytes = capacityMbps × wall-clock). Flag if the two counters diverge
// by more than VPN_DIVERGENCE_BPS (10%) of the larger — a sign one side is lying or a peer is broken.
export function reconcile(
  clientDelta: number,
  exitDelta: number,
  capacityBytes: number,
): { reconciledBytes: number; flagged: boolean } {
  const c = Math.max(0, clientDelta);
  const e = Math.max(0, exitDelta);
  // FAIL CLOSED: an unknown/zero capacity means we cannot bound the claim, so reward nothing
  // this step rather than defaulting to Infinity (which made the ceiling a no-op).
  const cap = capacityBytes > 0 ? capacityBytes : 0;
  const cCapped = Math.min(c, cap);
  const eCapped = Math.min(e, cap);
  const reconciledBytes = Math.min(cCapped, eCapped);
  const hi = Math.max(c, e);
  const flagged = hi > 0 && Math.abs(c - e) / hi > VPN_DIVERGENCE_BPS / 10_000;
  return { reconciledBytes, flagged };
}

// Bytes/second a link of `capacityMbps` can carry (1 Mbps = 125_000 bytes/s). capacityMbps is a
// self-declared, operator-controlled field, so clamp it to a sane ceiling before it can widen the cap.
function capacityBytesFor(capacityMbps: number, elapsedSeconds: number): number {
  const mbps = Math.min(Math.max(0, capacityMbps), VPN_MAX_CAPACITY_MBPS);
  return mbps * 125_000 * Math.max(0, elapsedSeconds);
}

function utcDayStart(now = new Date()): Date {
  const d = new Date(now);
  d.setUTCHours(0, 0, 0, 0);
  return d;
}

function clampBig(v: bigint, lo: bigint, hi: bigint): bigint {
  if (v < lo) return lo;
  if (v > hi) return hi;
  return v;
}

// Recompute a session's reconciled bytes + IOU reward from the latest client & exit receipts, and
// roll the delta onto the exit's IOU total. Idempotent: reward is recomputed from cumulative
// counters (like hosting recomputes from cumulative byte-hours), so replayed/duplicate reports don't
// double-credit. Returns null if only one side has reported (nothing to reconcile yet).
export async function reconcileSession(sessionId: string, now = new Date()): Promise<{
  reconciledBytes: number;
  flagged: boolean;
  sessionRewardNanm: bigint;
  incrementNanm: bigint;
  selfDeal: boolean;
} | null> {
  const session = await prisma.vpnSession.findUnique({
    where: { id: sessionId },
    include: { exit: { include: { owner: { select: { id: true } } } } },
  });
  if (!session) return null;

  // Latest cumulative counter from each side.
  const latestClient = await prisma.vpnUsageReceipt.findFirst({
    where: { sessionId, side: 'client' }, orderBy: { seq: 'desc' },
  });
  const latestExit = await prisma.vpnUsageReceipt.findFirst({
    where: { sessionId, side: 'exit' }, orderBy: { seq: 'desc' },
  });
  if (!latestClient || !latestExit) return null; // need both sides before we can reconcile

  const clientCum = latestClient.cumRx + latestClient.cumTx;
  const exitCum = latestExit.cumRx + latestExit.cumTx;

  const elapsedSeconds = Math.max(0, (now.getTime() - session.startedAt.getTime()) / 1000);
  const capBytes = capacityBytesFor(session.exit.capacityMbps, elapsedSeconds);
  const { reconciledBytes, flagged } = reconcile(Number(clientCum), Number(exitCum), capBytes);

  // Self-deal / sybil zeroing: if the consuming client wallet IS the exit owner, no reward.
  const selfDeal = session.clientAccountId === session.exit.ownerId;

  // Reward is recomputed from cumulative reconciled bytes, then bounded by the session ceiling.
  let sessionReward = selfDeal ? 0n : clampBig(vpnRewardNanm(reconciledBytes), 0n, VPN_SESSION_MAX_NANM);

  // Daily ceilings. Two hardening choices vs. the naive version:
  //  * key the per-node cap on the exit OWNER across ALL their exits, not on the freely-mintable
  //    exitId — otherwise a Sybil spins up N exits and multiplies the per-node cap by N.
  //  * window on lastReportAt (when reward actually ACCRUES), not startedAt — otherwise a session
  //    pre-created just before UTC midnight escapes every subsequent day's cap.
  const dayStart = utcDayStart(now);
  if (sessionReward > 0n) {
    const nodeAgg = await prisma.vpnSession.aggregate({
      _sum: { rewardNanm: true },
      where: {
        exit: { ownerId: session.exit.ownerId },
        lastReportAt: { gte: dayStart },
        id: { not: sessionId },
      },
    });
    const nodeOther = nodeAgg._sum.rewardNanm ?? 0n;
    const nodeAllowed = VPN_NODE_DAILY_MAX_NANM - nodeOther;
    if (sessionReward > nodeAllowed) sessionReward = nodeAllowed > 0n ? nodeAllowed : 0n;
  }
  // Global daily ceiling across all exits (accrual-time window).
  if (sessionReward > 0n) {
    const globalAgg = await prisma.vpnSession.aggregate({
      _sum: { rewardNanm: true },
      where: { lastReportAt: { gte: dayStart }, id: { not: sessionId } },
    });
    const globalOther = globalAgg._sum.rewardNanm ?? 0n;
    const globalAllowed = VPN_GLOBAL_DAILY_MAX_NANM - globalOther;
    if (sessionReward > globalAllowed) sessionReward = globalAllowed > 0n ? globalAllowed : 0n;
  }

  // Roll the session + exit IOU forward ATOMICALLY. Two receipts for the same session can reconcile
  // concurrently; a read-modify-write on the exit total would double-credit. Re-read the session's
  // rewardNanm inside the transaction and derive the increment from that fresh value so a concurrent
  // reconcile serialises instead of racing.
  const nextStatus = session.status === 'CLOSED' ? 'CLOSED' : flagged ? 'FLAGGED' : 'ACTIVE';
  const finalReward = sessionReward;
  const incrementNanm = await prisma.$transaction(async (tx) => {
    const fresh = await tx.vpnSession.findUnique({ where: { id: sessionId }, select: { rewardNanm: true } });
    const prevReward = fresh?.rewardNanm ?? session.rewardNanm;
    const inc = finalReward - prevReward;
    await tx.vpnSession.update({
      where: { id: sessionId },
      data: {
        clientCumBytes: clientCum,
        exitCumBytes: exitCum,
        reconciledBytes: BigInt(Math.floor(reconciledBytes)),
        rewardNanm: finalReward,
        status: nextStatus,
        lastReportAt: now,
      },
    });
    if (inc !== 0n) {
      await tx.vpnExit.update({
        where: { id: session.exitId },
        data: { rewardNanm: { increment: inc } },
      });
    }
    return inc;
  });

  return { reconciledBytes, flagged, sessionRewardNanm: finalReward, incrementNanm, selfDeal };
}

// Mark exits offline if they haven't heartbeat within the staleness window (called opportunistically).
export async function reapStaleExits(): Promise<number> {
  const cutoff = new Date(Date.now() - VPN_STALE_SECONDS * 1000);
  const res = await prisma.vpnExit.updateMany({
    where: { online: true, lastSeenAt: { lt: cutoff } },
    data: { online: false },
  });
  return res.count;
}

// Deterministically allocate the next free /32 in 10.99.0.0/16 for this exit, starting at 10.99.0.2.
// Collision-safe: only ACTIVE sessions hold an IP, and VpnSession has a @@unique([exitId, allocatedIp])
// so a racing create fails and the caller retries.
export async function allocateIp(exitId: string): Promise<string> {
  const active = await prisma.vpnSession.findMany({
    where: { exitId, status: 'ACTIVE' },
    select: { allocatedIp: true },
  });
  const used = new Set(active.map((s) => s.allocatedIp));
  // 10.99.0.0/16 host range; skip .0 and .255 fourth-octet for cleanliness.
  for (let i = 2; i <= 65_533; i += 1) {
    const third = (i >> 8) & 0xff;
    const fourth = i & 0xff;
    if (fourth === 0 || fourth === 255) continue;
    const ip = `10.99.${third}.${fourth}`;
    if (!used.has(ip)) return ip;
  }
  throw new Error('10.99.0.0/16 address pool exhausted for this exit');
}
