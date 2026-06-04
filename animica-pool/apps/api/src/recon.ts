// Standalone reconciliation worker (run: node dist/recon.js via systemd).
// Closes loops the webhook path doesn't: completes ended rentals (freeing the
// assigned worker) and polls in-flight NOWPayments payout batches.
import "reflect-metadata";
import { PrismaClient } from "@prisma/client";
import { PayoutsService } from "./payouts/payouts.service";

const prisma = new PrismaClient();
const payouts = new PayoutsService(prisma as never);
const TICK_MS = Number(process.env.RECON_TICK_MS || 30000);

async function completeEndedRentals() {
  const ended = await prisma.rentalOrder.findMany({ where: { status: "active", endsAt: { lte: new Date() } } });
  for (const o of ended) {
    await prisma.rentalOrder.update({ where: { id: o.id }, data: { status: "completed" } });
    if (o.assignedWorkerId) {
      await prisma.worker.updateMany({ where: { id: o.assignedWorkerId, status: "busy" }, data: { status: "online" } }).catch(() => {});
    }
  }
  if (ended.length) console.log(`[recon] completed ${ended.length} ended rental(s)`);
}

async function pollPayoutBatches() {
  const batches = await prisma.payoutBatch.findMany({
    where: { status: { in: ["submitted", "processing"] }, nowpaymentsBatchId: { not: null } },
  });
  for (const b of batches) {
    await payouts.pollBatch(b.id).catch((e) => console.error(`[recon] pollBatch ${b.id}: ${e}`));
  }
  if (batches.length) console.log(`[recon] polled ${batches.length} payout batch(es)`);
}

async function tick() {
  await Promise.allSettled([completeEndedRentals(), pollPayoutBatches()]);
}

async function main() {
  console.log(`[recon] starting, tick=${TICK_MS}ms`);
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try { await tick(); } catch (e) { console.error("[recon] tick error", e); }
    await new Promise((r) => setTimeout(r, TICK_MS));
  }
}

main();
