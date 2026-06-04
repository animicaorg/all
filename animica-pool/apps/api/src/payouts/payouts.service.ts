import { Injectable, BadRequestException, NotFoundException } from "@nestjs/common";
import { Prisma, PayoutAsset, PayoutType, PayoutStatus } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";
import { createMassPayout, getMassPayout } from "../payments/nowpayments.client";
import { usdToAsset } from "./pricing";
import { sendAnm, anmPayoutsEnabled } from "./animica.client";

const MIN_PAYOUT_USD = 5;
// NOWPayments mass-payout currency per asset (priced via the oracle). ANM is
// paid on-chain via the Animica node; SPLIT/ORIGINAL resolve manually.
const NP_CURRENCY: Partial<Record<PayoutAsset, string>> = {
  USDT: "usdttrc20", BTC: "btc", SOL: "sol", XMR: "xmr",
};

@Injectable()
export class PayoutsService {
  constructor(private readonly prisma: PrismaService) {}

  // ---- Wallets ----
  listWallets(userId: string) {
    return this.prisma.payoutWallet.findMany({ where: { userId }, orderBy: { createdAt: "desc" } });
  }

  async addWallet(userId: string, dto: { asset: string; address: string; isDefault?: boolean }) {
    if (dto.isDefault) await this.prisma.payoutWallet.updateMany({ where: { userId }, data: { isDefault: false } });
    return this.prisma.payoutWallet.create({
      data: { userId, asset: dto.asset as PayoutAsset, address: dto.address, isDefault: dto.isDefault ?? false },
    });
  }

  // ---- Payable balance (worker/compute earnings) ----
  async payableBalanceUsd(userId: string): Promise<number> {
    const workers = await this.prisma.worker.findMany({ where: { userId }, select: { id: true } });
    const ids = workers.map((w) => w.id);
    const earned = ids.length
      ? await this.prisma.workerJob.aggregate({ _sum: { rewardUsd: true }, where: { workerId: { in: ids }, status: "completed" } })
      : { _sum: { rewardUsd: null } };
    const earnedUsd = Number(earned._sum.rewardUsd ?? 0);
    // Bittensor SN51 owner shares: only RELEASED rows count — earnings inside
    // the holdback window stand behind the pool's on-chain executor
    // collateral (burned-on-slash) and are forfeited if that rig is slashed.
    const holdbackCutoff = new Date(Date.now() - env().BITTENSOR_HOLDBACK_DAYS * 24 * 60 * 60 * 1000);
    const btEarned = ids.length
      ? await this.prisma.bittensorEarning.aggregate({
          _sum: { ownerShareUsd: true },
          where: { forfeited: false, createdAt: { lte: holdbackCutoff }, executor: { workerId: { in: ids } } },
        })
      : { _sum: { ownerShareUsd: null } };
    const btUsd = Number(btEarned._sum.ownerShareUsd ?? 0);
    // Subtract amounts already requested (anything not rejected/cancelled).
    const reqd = await this.prisma.payoutRequest.aggregate({
      _sum: { amountUsd: true },
      where: { userId, status: { notIn: ["cancelled"] } },
    });
    return Math.max(0, earnedUsd + btUsd - Number(reqd._sum.amountUsd ?? 0));
  }

  // ---- Requests (user) ----
  listRequests(userId: string) {
    return this.prisma.payoutRequest.findMany({ where: { userId }, orderBy: { createdAt: "desc" } });
  }

  async createRequest(userId: string, dto: { type: string; asset: string; address: string; amountUsd: number }) {
    if (!dto.amountUsd || dto.amountUsd < MIN_PAYOUT_USD) {
      throw new BadRequestException(`Minimum payout is $${MIN_PAYOUT_USD}`);
    }
    // For earnings-based types, enforce payable balance.
    if (["worker_inference", "compute_rental", "mining", "bittensor_mining"].includes(dto.type)) {
      const payable = await this.payableBalanceUsd(userId);
      if (dto.amountUsd > payable + 1e-9) {
        throw new BadRequestException(`Requested $${dto.amountUsd} exceeds payable balance $${payable.toFixed(2)}`);
      }
    }
    const req = await this.prisma.payoutRequest.create({
      data: {
        userId, type: dto.type as PayoutType, asset: dto.asset as PayoutAsset,
        address: dto.address, amountUsd: new Prisma.Decimal(dto.amountUsd),
        status: "pending_review",
      },
    });
    await auditLog(this.prisma, { action: "payout.request", entityType: "PayoutRequest", entityId: req.id, actor: userId, metadata: { amountUsd: dto.amountUsd, asset: dto.asset } });

    // AUTO_PAYOUTS_ENABLED: skip manual review — auto-approve and submit now.
    // SAFETY: only auto-submit earnings-backed types (bounded by the verified
    // payable balance above); referral/treasury/anm_reward still need manual
    // admin approval so an unbacked request can never auto-drain custody.
    // Funds only move when NOWPAYMENTS_PAYOUTS_ENABLED=true (crypto) or
    // ANIMICA_RPC_URL is set (ANM on-chain).
    const autoEligible = ["worker_inference", "compute_rental", "mining", "bittensor_mining"].includes(dto.type);
    if (env().AUTO_PAYOUTS_ENABLED && autoEligible) {
      try {
        await this.approve("auto", req.id);
        const result = await this.createBatch("auto", [req.id]);
        await auditLog(this.prisma, { action: "payout.auto_submitted", entityType: "PayoutRequest", entityId: req.id, actor: "auto", metadata: { submitted: result.submitted } });
      } catch (e) {
        await auditLog(this.prisma, { action: "payout.auto_failed", entityType: "PayoutRequest", entityId: req.id, metadata: { error: String(e) } });
      }
      return this.prisma.payoutRequest.findUnique({ where: { id: req.id } });
    }
    return req;
  }

  // ---- Admin ----
  listForAdmin(status?: string) {
    return this.prisma.payoutRequest.findMany({
      where: status ? { status: status as PayoutStatus } : undefined,
      orderBy: { createdAt: "desc" }, take: 200, include: { user: { select: { email: true } } },
    });
  }

  async approve(adminEmail: string, id: string) {
    const req = await this.prisma.payoutRequest.findUnique({ where: { id } });
    if (!req) throw new NotFoundException();
    if (req.status !== "pending_review" && req.status !== "pending") throw new BadRequestException(`Cannot approve a ${req.status} payout`);
    const updated = await this.prisma.payoutRequest.update({ where: { id }, data: { status: "approved", reviewedBy: adminEmail } });
    await auditLog(this.prisma, { action: "payout.approve", entityType: "PayoutRequest", entityId: id, actor: adminEmail });
    return updated;
  }

  async reject(adminEmail: string, id: string, note?: string) {
    const updated = await this.prisma.payoutRequest.update({ where: { id }, data: { status: "cancelled", reviewedBy: adminEmail, note } });
    await auditLog(this.prisma, { action: "payout.reject", entityType: "PayoutRequest", entityId: id, actor: adminEmail, metadata: { note } });
    return updated;
  }

  /** Create a batch from approved requests. SAFETY: only submits to NOWPayments
   *  when NOWPAYMENTS_PAYOUTS_ENABLED=true; otherwise the batch is recorded for
   *  manual processing and NO funds move. Automatic payouts are never run. */
  /** Rolling-24h payout usage vs the ANM-equivalent daily cap. */
  async dailyCapStatus(addUsd: number) {
    const e = env();
    const capAnm = e.DAILY_PAYOUT_CAP_ANM;
    const anmPrice = Number(e.ANM_USD_PRICE || 0);
    const capUsd = capAnm > 0 && anmPrice > 0 ? capAnm * anmPrice : Infinity;
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const agg = await this.prisma.payoutRequest.aggregate({
      _sum: { amountUsd: true },
      where: { status: { in: ["submitted", "processing", "completed"] }, updatedAt: { gte: since } },
    });
    const usedUsd = Number(agg._sum.amountUsd ?? 0);
    const anm = (usd: number) => (anmPrice > 0 ? usd / anmPrice : 0);
    return { ok: usedUsd + addUsd <= capUsd + 1e-9, capAnm, capUsd, usedUsd, usedAnm: anm(usedUsd), addUsd, addAnm: anm(addUsd) };
  }

  async createBatch(adminEmail: string, requestIds: string[]) {
    const reqs = await this.prisma.payoutRequest.findMany({ where: { id: { in: requestIds }, status: "approved" } });
    if (reqs.length === 0) throw new BadRequestException("No approved requests in selection");
    const totalUsd = reqs.reduce((s, r) => s + Number(r.amountUsd), 0);

    // SAFETY: rolling-24h ANM-equivalent cap. Over-cap → hold, move NO funds.
    const cap = await this.dailyCapStatus(totalUsd);
    if (!cap.ok) {
      await auditLog(this.prisma, { action: "payout.daily_cap_hit", entityType: "PayoutBatch", actor: adminEmail, metadata: cap });
      return {
        submitted: false,
        note: `Daily payout cap reached: ${cap.capAnm.toLocaleString()} ANM/day (~$${cap.capUsd.toFixed(2)}). Used ${cap.usedAnm.toFixed(0)} ANM in the last 24h; this batch (${cap.addAnm.toFixed(0)} ANM) is held. It stays 'approved' — re-batch after the window or settle manually.`,
      };
    }

    const batch = await this.prisma.payoutBatch.create({
      data: { status: "pending", totalUsd: new Prisma.Decimal(totalUsd), createdBy: adminEmail },
    });
    await this.prisma.payoutRequest.updateMany({ where: { id: { in: reqs.map((r) => r.id) } }, data: { batchId: batch.id } });

    const notes: string[] = [];

    // 1) ANM requests settle on-chain via the Animica node (if configured).
    const anmReqs = reqs.filter((r) => r.asset === "ANM");
    let anmPaid = 0;
    if (anmReqs.length && anmPayoutsEnabled()) {
      const anmUsd = Number(env().ANM_USD_PRICE || 0);
      for (const r of anmReqs) {
        try {
          const amountAnm = anmUsd > 0 ? (Number(r.amountUsd) / anmUsd).toFixed(9) : "0";
          const txid = await sendAnm(r.address, amountAnm);
          await this.prisma.payoutRequest.update({ where: { id: r.id }, data: { status: "completed", note: `anm tx ${txid}` } });
          anmPaid++;
        } catch (e) {
          await this.prisma.payoutRequest.update({ where: { id: r.id }, data: { status: "failed", note: String(e) } });
        }
      }
      notes.push(`${anmPaid}/${anmReqs.length} ANM paid on-chain`);
    } else if (anmReqs.length) {
      notes.push(`${anmReqs.length} ANM requests pending (ANIMICA_RPC_URL not set — settle manually)`);
    }

    // 2) Crypto (BTC/SOL/XMR/USDT) via NOWPayments mass payout — gated.
    const cryptoReqs = reqs.filter((r) => NP_CURRENCY[r.asset]);
    if (!env().NOWPAYMENTS_PAYOUTS_ENABLED) {
      if (cryptoReqs.length) notes.push(`${cryptoReqs.length} crypto requests recorded for manual processing (NOWPAYMENTS_PAYOUTS_ENABLED=false)`);
      await auditLog(this.prisma, { action: "payout.batch.manual", entityType: "PayoutBatch", entityId: batch.id, actor: adminEmail, metadata: { totalUsd, anmPaid } });
      return { batch, submitted: false, note: notes.join("; ") || "Batch recorded for manual processing." };
    }

    const withdrawals = await Promise.all(
      cryptoReqs.map(async (r) => ({
        address: r.address, currency: NP_CURRENCY[r.asset]!,
        amount: await usdToAsset(r.asset, Number(r.amountUsd)), unique_external_id: r.id,
      })),
    );
    if (withdrawals.length === 0) {
      return { batch, submitted: anmPaid > 0, note: notes.join("; ") || "No crypto requests to submit." };
    }
    const np = await createMassPayout({ withdrawals, ipnCallbackUrl: env().NOWPAYMENTS_PUBLIC_CALLBACK_URL || undefined });
    const updated = await this.prisma.payoutBatch.update({ where: { id: batch.id }, data: { status: "submitted", nowpaymentsBatchId: np.id } });
    await this.prisma.payoutRequest.updateMany({ where: { id: { in: cryptoReqs.map((r) => r.id) } }, data: { status: "submitted" } });
    await auditLog(this.prisma, { action: "payout.batch.submitted", entityType: "PayoutBatch", entityId: batch.id, actor: adminEmail, metadata: { npBatchId: np.id, count: withdrawals.length, anmPaid } });
    notes.push(`${withdrawals.length} crypto payouts submitted to NOWPayments`);
    return { batch: updated, submitted: true, npBatchId: np.id, note: notes.join("; ") };
  }

  async pollBatch(batchId: string) {
    const batch = await this.prisma.payoutBatch.findUnique({ where: { id: batchId } });
    if (!batch?.nowpaymentsBatchId) throw new NotFoundException("No NOWPayments batch id");
    const np = await getMassPayout(batch.nowpaymentsBatchId);
    return this.applyBatchStatus(batch.id, np.status);
  }

  /** Webhook/poll → terminal batch status. */
  async applyBatchStatus(batchId: string, npStatus: string) {
    const map: Record<string, PayoutStatus> = {
      FINISHED: "completed", FAILED: "failed", REJECTED: "failed", EXPIRED: "failed",
      PROCESSING: "processing", SENDING: "processing", WAITING: "submitted",
    };
    const status = map[npStatus] ?? "submitted";
    await this.prisma.payoutBatch.update({ where: { id: batchId }, data: { status } });
    if (status === "completed" || status === "failed") {
      await this.prisma.payoutRequest.updateMany({ where: { batchId }, data: { status } });
    }
    return { batchId, status };
  }

  async handlePayoutIpn(payload: Record<string, unknown>): Promise<boolean> {
    const npBatchId = String(payload.id ?? payload.batch_withdrawal_id ?? "");
    const npStatus = String(payload.status ?? payload.batch_withdrawal_status ?? "");
    if (!npBatchId) return false;
    const batch = await this.prisma.payoutBatch.findFirst({ where: { nowpaymentsBatchId: npBatchId } });
    if (!batch) return false;
    await this.applyBatchStatus(batch.id, npStatus);
    return true;
  }
}
