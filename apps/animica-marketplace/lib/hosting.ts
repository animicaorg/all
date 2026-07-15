import { prisma } from './db';
import { HOSTING_NANM_PER_BYTE_HOUR, HOSTING_STALE_SECONDS } from './config';

// Hosting-reward accrual for content-replicating nodes. Reward = Σ(bytes × hours-available), paid at
// a fixed rate. IMPORTANT: this accrues an IOU (HostingPin.rewardNanm) — it is NOT credited to the
// spendable ledger. Real settlement is treasury-funded and follows the same deferred model as AICF
// inference pay (see memory: AICF pay is IOU-only today). Never present this as an executed transfer.

export function hostingRewardNanm(byteHours: number): bigint {
  return BigInt(Math.floor(byteHours * HOSTING_NANM_PER_BYTE_HOUR));
}

// Advance a pin's accrual from lastSeen to `now`, capped so a long gap (offline) doesn't over-credit.
// Returns the incremental byte-hours + nanm added.
export function accrue(pin: { bytes: number; lastSeen: Date; byteHours: number }, now = new Date()) {
  const elapsedS = Math.max(0, (now.getTime() - pin.lastSeen.getTime()) / 1000);
  // Only credit availability up to the staleness window — beyond that we can't prove it was hosted.
  const creditedS = Math.min(elapsedS, HOSTING_STALE_SECONDS);
  const addByteHours = (pin.bytes * creditedS) / 3600;
  return { addByteHours, elapsedS };
}

// Record/refresh a node's pin on a CID and roll its reward forward. Idempotent per (cid,node).
export async function heartbeatPin(opts: {
  cid: string; nodeId: string; accountId?: string | null; bytes: number;
}): Promise<{ byteHours: number; rewardNanm: bigint; totalPins: number }> {
  const now = new Date();
  const existing = await prisma.hostingPin.findUnique({ where: { cid_nodeId: { cid: opts.cid, nodeId: opts.nodeId } } });

  let byteHours: number;
  if (existing) {
    const { addByteHours } = accrue({ bytes: existing.bytes, lastSeen: existing.lastSeen, byteHours: existing.byteHours }, now);
    byteHours = existing.byteHours + addByteHours;
    await prisma.hostingPin.update({
      where: { id: existing.id },
      data: {
        lastSeen: now, active: true, byteHours,
        rewardNanm: hostingRewardNanm(byteHours),
        accountId: opts.accountId ?? existing.accountId,
      },
    });
  } else {
    byteHours = 0;
    await prisma.hostingPin.create({
      data: { cid: opts.cid, nodeId: opts.nodeId, accountId: opts.accountId ?? null, bytes: opts.bytes },
    });
  }
  // Denormalize the live replica count onto the object.
  const totalPins = await prisma.hostingPin.count({ where: { cid: opts.cid, active: true } });
  await prisma.contentObject.update({ where: { cid: opts.cid }, data: { pins: totalPins } }).catch(() => {});
  return { byteHours, rewardNanm: hostingRewardNanm(byteHours), totalPins };
}

// Mark pins inactive if they haven't heartbeat within the staleness window (called opportunistically).
export async function reapStalePins(): Promise<number> {
  const cutoff = new Date(Date.now() - HOSTING_STALE_SECONDS * 1000);
  const res = await prisma.hostingPin.updateMany({ where: { active: true, lastSeen: { lt: cutoff } }, data: { active: false } });
  return res.count;
}
