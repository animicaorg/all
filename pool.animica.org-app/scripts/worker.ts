// scripts/worker.ts — rental settlement worker.
//
// Pollers (all idempotent, run together via Promise.allSettled each tick):
//   1. expireStaleQuotes   — unpaid quotes past TTL+grace → EXPIRED
//   2. activateConfirmed   — PAYMENT_CONFIRMED → engage pool redirect → ACTIVE
//   3. monitorActive       — track measured uptime; offline beyond grace → REFUND_DUE
//   4. completeEnded       — window elapsed → COMPLETING (or REFUND_DUE if under-delivered)
//   5. payOwners           — COMPLETING → pay owner 95% (auto-convert) → OWNER_PAID
//   6. processRefunds      — REFUND_DUE → pay owner pro-rata + refund renter → REFUND_PROCESSING
//   7. pollPayouts         — poll NOWPayments payout/refund status → COMPLETE / REFUNDED
//
// The marketplace 5% fee is on the rental price; the pool's 5% is on mined
// rewards (which go to the renter). Disjoint — see src/server/rentals.ts.

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Minimal .env loader (the standalone worker isn't wired by Next). systemd
// supplies these via EnvironmentFile in production; this covers local runs.
try {
  const envPath = resolve(process.cwd(), ".env");
  for (const line of readFileSync(envPath, "utf8").split("\n")) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m && process.env[m[1]] === undefined) {
      process.env[m[1]] = m[2].replace(/^["']|["']$/g, "");
    }
  }
} catch {
  /* no .env file — rely on the process environment */
}

import { prisma } from "../src/server/db";
import { env } from "../src/server/env";
import { poolClient } from "../src/server/poolClient";
import { transition, settle } from "../src/server/rentals";
import { createMassPayout, getMassPayout, getMinAmount } from "../src/server/nowpayments";
import { auditLog } from "../src/lib/audit";
import { D, gt } from "../src/lib/money";

const TICK_MS = Number(process.env.WORKER_TICK_MS || 10_000);
const QUOTE_GRACE_MS = 60 * 60 * 1000; // 1h grace after quote TTL

function nowSec(): number {
  return Math.floor(Date.now() / 1000);
}

async function expireStaleQuotes(): Promise<void> {
  const cutoff = new Date(Date.now() - QUOTE_GRACE_MS);
  const stale = await prisma.rental.findMany({
    where: {
      status: { in: ["CREATED", "QUOTED", "WAITING_PAYMENT"] },
      quoteExpiresAt: { lt: cutoff },
    },
  });
  for (const r of stale) {
    await transition({ rentalId: r.id, to: "EXPIRED", reasonMetadata: { reason: "quote_expired" } }).catch(
      () => {},
    );
  }
}

async function activateConfirmed(): Promise<void> {
  const confirmed = await prisma.rental.findMany({
    where: { status: "PAYMENT_CONFIRMED" },
    include: { rig: true },
  });
  for (const r of confirmed) {
    const startSec = nowSec();
    const endSec = startSec + r.hours * 3600;
    const coins = r.coins as "ANM" | "XMR" | "BOTH";
    const wantsAnm = coins === "ANM" || coins === "BOTH";
    const wantsXmr = coins === "XMR" || coins === "BOTH";
    try {
      if (wantsXmr && r.renterAnmAddress && r.renterXmrAddress) {
        // Route the rig's redirected XMR credit (keyed to the renter's anim1
        // handle) to the renter's Monero payout address.
        await poolClient.xmrRegister(r.renterAnmAddress, r.renterXmrAddress);
      }
      await poolClient.createAssignment({
        rentalId: r.id,
        ownerWorker: r.rig.workerName,
        ownerAddress: r.rig.ownerAddress,
        coins,
        startTs: startSec,
        endTs: endSec,
        renterAnmAddress: wantsAnm ? r.renterAnmAddress : null,
        renterXmrAnimAddress: wantsXmr ? r.renterAnmAddress : null,
        anmMode: (r.anmMode as "pps" | "solo" | null) ?? null,
      });
    } catch (err) {
      if (poolClient.isConflict(err)) {
        await transition({
          rentalId: r.id,
          to: "NEEDS_ADMIN_REVIEW",
          reasonMetadata: { reason: "pool_assignment_conflict", error: String(err) },
        }).catch(() => {});
        continue;
      }
      // Pool unreachable — leave in PAYMENT_CONFIRMED, retry next tick.
      await auditLog({
        action: "rental.activate_failed",
        entityType: "Rental",
        entityId: r.id,
        metadata: { error: String(err) },
      });
      continue;
    }
    await transition({
      rentalId: r.id,
      to: "ACTIVE",
      patch: {
        windowStartAt: new Date(startSec * 1000),
        windowEndAt: new Date(endSec * 1000),
      },
    }).catch(() => {});
  }
}

async function monitorActive(): Promise<void> {
  const active = await prisma.rental.findMany({ where: { status: "ACTIVE" } });
  const graceSec = env().RIG_OFFLINE_GRACE_SECONDS;
  for (const r of active) {
    if (!r.windowStartAt) continue;
    let info;
    try {
      info = await poolClient.getAssignment(r.id);
    } catch {
      continue; // transient pool error — try next tick
    }
    if (!info) continue;
    const measured = info.window.measured_uptime_seconds;
    await prisma.rental.update({
      where: { id: r.id },
      data: { measuredUptimeSeconds: measured },
    });
    // Cumulative downtime beyond grace (and window not yet over) → refund path.
    const elapsed = nowSec() - Math.floor(r.windowStartAt.getTime() / 1000);
    const downtime = Math.max(0, elapsed - measured);
    const windowOver = r.windowEndAt ? Date.now() >= r.windowEndAt.getTime() : false;
    if (!windowOver && downtime > graceSec && !info.rig?.online) {
      await poolClient.cancelAssignment(r.id).catch(() => {});
      await transition({
        rentalId: r.id,
        to: "REFUND_DUE",
        reasonMetadata: { reason: "rig_offline", downtime, measured },
      }).catch(() => {});
    }
  }
}

async function completeEnded(): Promise<void> {
  const ended = await prisma.rental.findMany({
    where: { status: "ACTIVE", windowEndAt: { lte: new Date() } },
  });
  for (const r of ended) {
    let measured = r.measuredUptimeSeconds ?? 0;
    try {
      const info = await poolClient.getAssignment(r.id);
      if (info) measured = info.window.measured_uptime_seconds;
      await poolClient.cancelAssignment(r.id); // idempotent with pool expiry sweep
    } catch {
      /* pool error — proceed with last-known measured */
    }
    const windowSec = r.hours * 3600;
    const delivered = windowSec > 0 ? measured / windowSec : 0;
    const next = delivered >= 0.999 ? "COMPLETING" : "REFUND_DUE";
    await transition({
      rentalId: r.id,
      to: next,
      patch: { measuredUptimeSeconds: measured },
      reasonMetadata: { delivered },
    }).catch(() => {});
  }
}

async function payoutMinOk(currency: string, amountUsd: string): Promise<boolean> {
  // Best-effort min-amount guard. If the estimate can't be fetched, allow.
  try {
    const min = await getMinAmount({ currency_from: "usd", currency_to: currency });
    return D(amountUsd).gte(D(min.fiat_equivalent ?? 0));
  } catch {
    return true;
  }
}

async function payOwners(): Promise<void> {
  const due = await prisma.rental.findMany({ where: { status: "COMPLETING" } });
  for (const r of due) {
    const amount = r.ownerNetUsd;
    if (!r.ownerPayoutAddress || !r.ownerPayoutCurrency) {
      await transition({ rentalId: r.id, to: "NEEDS_ADMIN_REVIEW", reasonMetadata: { reason: "missing_owner_payout" } }).catch(() => {});
      continue;
    }
    if (!gt(amount, "0") || !(await payoutMinOk(r.ownerPayoutCurrency, amount))) {
      await transition({ rentalId: r.id, to: "NEEDS_ADMIN_REVIEW", reasonMetadata: { reason: "owner_payout_below_min", amount } }).catch(() => {});
      continue;
    }
    try {
      const batch = await createMassPayout({
        ipn_callback_url: env().NOWPAYMENTS_PUBLIC_CALLBACK_URL,
        withdrawals: [
          {
            address: r.ownerPayoutAddress,
            currency: r.ownerPayoutCurrency,
            amount,
            unique_external_id: `owner-${r.id}`,
            payout_description: `Rig rental payout ${r.id}`,
          },
        ],
      });
      await transition({
        rentalId: r.id,
        to: "OWNER_PAID",
        patch: { npPayoutId: batch.id, ownerEarnedUsd: amount },
      }).catch(() => {});
    } catch (err) {
      await auditLog({ action: "rental.owner_payout_failed", entityType: "Rental", entityId: r.id, metadata: { error: String(err) } });
    }
  }
}

async function processRefunds(): Promise<void> {
  const due = await prisma.rental.findMany({ where: { status: "REFUND_DUE" } });
  for (const r of due) {
    const s = settle({
      grossUsd: r.grossUsd,
      ownerNetUsd: r.ownerNetUsd,
      marketplaceFeeUsd: r.marketplaceFeeUsd,
      measuredUptimeSeconds: r.measuredUptimeSeconds ?? 0,
      hours: r.hours,
    });
    // Pay the owner for delivered time (if any).
    let ownerPayoutId: string | null = null;
    if (gt(s.ownerEarnedUsd, "0") && r.ownerPayoutAddress && r.ownerPayoutCurrency) {
      try {
        const batch = await createMassPayout({
          ipn_callback_url: env().NOWPAYMENTS_PUBLIC_CALLBACK_URL,
          withdrawals: [
            {
              address: r.ownerPayoutAddress,
              currency: r.ownerPayoutCurrency,
              amount: s.ownerEarnedUsd,
              unique_external_id: `owner-${r.id}`,
            },
          ],
        });
        ownerPayoutId = batch.id;
      } catch (err) {
        await auditLog({ action: "rental.refund_owner_payout_failed", entityType: "Rental", entityId: r.id, metadata: { error: String(err) } });
      }
    }
    // Refund the renter for undelivered time. Needs a renter refund address
    // (captured at rent time into metadata); otherwise route to admin.
    const md = (r.metadata as Record<string, unknown> | null) ?? {};
    const refundAddr = typeof md.renterRefundAddress === "string" ? md.renterRefundAddress : "";
    const refundCcy = typeof md.renterRefundCurrency === "string" ? md.renterRefundCurrency : "";
    let refundPayoutId: string | null = null;
    if (gt(s.refundUsd, "0") && refundAddr && refundCcy) {
      try {
        const batch = await createMassPayout({
          ipn_callback_url: env().NOWPAYMENTS_PUBLIC_CALLBACK_URL,
          withdrawals: [
            { address: refundAddr, currency: refundCcy, amount: s.refundUsd, unique_external_id: `refund-${r.id}` },
          ],
        });
        refundPayoutId = batch.id;
      } catch (err) {
        await auditLog({ action: "rental.refund_payout_failed", entityType: "Rental", entityId: r.id, metadata: { error: String(err) } });
      }
    }
    if (!refundPayoutId && gt(s.refundUsd, "0")) {
      // No refund address on file — owner (maybe) paid, renter refund manual.
      await transition({
        rentalId: r.id,
        to: "NEEDS_ADMIN_REVIEW",
        patch: { ownerEarnedUsd: s.ownerEarnedUsd, refundUsd: s.refundUsd, npPayoutId: ownerPayoutId },
        reasonMetadata: { reason: "manual_refund_required", ...s },
      }).catch(() => {});
      continue;
    }
    await transition({
      rentalId: r.id,
      to: "REFUND_PROCESSING",
      patch: {
        ownerEarnedUsd: s.ownerEarnedUsd,
        refundUsd: s.refundUsd,
        npPayoutId: ownerPayoutId,
        npRefundPayoutId: refundPayoutId,
      },
      reasonMetadata: { ...s },
    }).catch(() => {});
  }
}

async function pollPayouts(): Promise<void> {
  const inflight = await prisma.rental.findMany({
    where: { status: { in: ["OWNER_PAID", "REFUND_PROCESSING"] } },
  });
  for (const r of inflight) {
    const ids = [r.npPayoutId, r.npRefundPayoutId].filter(Boolean) as string[];
    for (const id of ids) {
      let batch;
      try {
        batch = await getMassPayout(id);
      } catch {
        continue;
      }
      if (batch.status === "FINISHED") {
        const to = r.status === "OWNER_PAID" ? "COMPLETE" : "REFUNDED";
        await transition({ rentalId: r.id, to, reasonMetadata: { payoutId: id } }).catch(() => {});
        if (to === "REFUNDED") {
          await transition({ rentalId: r.id, to: "COMPLETE" }).catch(() => {});
        }
      } else if (batch.status === "FAILED" || batch.status === "REJECTED") {
        await transition({
          rentalId: r.id,
          to: "NEEDS_ADMIN_REVIEW",
          reasonMetadata: { reason: `payout_${batch.status}`, payoutId: id },
        }).catch(() => {});
      }
    }
  }
}

async function tick(): Promise<void> {
  await Promise.allSettled([
    expireStaleQuotes(),
    activateConfirmed(),
    monitorActive(),
    completeEnded(),
    payOwners(),
    processRefunds(),
    pollPayouts(),
  ]);
}

async function main(): Promise<void> {
  // eslint-disable-next-line no-console
  console.log(`[rental-worker] starting, tick=${TICK_MS}ms`);
  // eslint-disable-next-line no-constant-condition
  while (true) {
    try {
      await tick();
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("[rental-worker] tick error", err);
    }
    await new Promise((res) => setTimeout(res, TICK_MS));
  }
}

main();
