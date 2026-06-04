import { Injectable, BadRequestException } from "@nestjs/common";
import { Prisma, RevenueSourceType } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";

interface Split { providers: number; treasury: number; referral: number }

@Injectable()
export class RevenueService {
  constructor(private readonly prisma: PrismaService) {}

  async getSplit(): Promise<Split> {
    const rows = await this.prisma.setting.findMany({ where: { key: { in: ["SPLIT_PROVIDERS_PERCENT", "SPLIT_TREASURY_PERCENT", "SPLIT_REFERRAL_PERCENT"] } } });
    const m = new Map(rows.map((r) => [r.key, Number(r.value)]));
    const e = env();
    return {
      providers: m.get("SPLIT_PROVIDERS_PERCENT") ?? e.SPLIT_PROVIDERS_PERCENT,
      treasury: m.get("SPLIT_TREASURY_PERCENT") ?? e.SPLIT_TREASURY_PERCENT,
      referral: m.get("SPLIT_REFERRAL_PERCENT") ?? e.SPLIT_REFERRAL_PERCENT,
    };
  }

  async setSplit(adminEmail: string, split: Split) {
    const total = split.providers + split.treasury + split.referral;
    if (Math.abs(total - 100) > 0.01) throw new BadRequestException(`Split must sum to 100 (got ${total})`);
    for (const [key, value] of [
      ["SPLIT_PROVIDERS_PERCENT", split.providers], ["SPLIT_TREASURY_PERCENT", split.treasury], ["SPLIT_REFERRAL_PERCENT", split.referral],
    ] as const) {
      await this.prisma.setting.upsert({ where: { key }, create: { key, value: String(value) }, update: { value: String(value) } });
    }
    await auditLog(this.prisma, { action: "revenue.split", entityType: "Setting", actor: adminEmail, metadata: split });
    return split;
  }

  /** Record net realized revenue + redistribution allocation. */
  async record(input: { sourceType: RevenueSourceType; sourceId?: string; grossUsd: number; costUsd?: number; feeUsd?: number }) {
    const cost = input.costUsd ?? 0;
    const fee = input.feeUsd ?? 0;
    const net = Math.max(0, input.grossUsd - cost - fee);
    const split = await this.getSplit();
    await this.prisma.revenueLedger.create({
      data: {
        sourceType: input.sourceType, sourceId: input.sourceId,
        grossUsd: new Prisma.Decimal(input.grossUsd), costUsd: new Prisma.Decimal(cost), feeUsd: new Prisma.Decimal(fee),
        netUsd: new Prisma.Decimal(net),
        allocationJson: {
          providersUsd: +(net * split.providers / 100).toFixed(8),
          treasuryUsd: +(net * split.treasury / 100).toFixed(8),
          referralUsd: +(net * split.referral / 100).toFixed(8),
        },
      },
    }).catch(() => {});
  }

  private async windowSum(sinceMs: number) {
    const since = new Date(Date.now() - sinceMs);
    const agg = await this.prisma.revenueLedger.aggregate({ _sum: { grossUsd: true, costUsd: true, netUsd: true }, where: { createdAt: { gte: since } } });
    return {
      grossUsd: Number(agg._sum.grossUsd ?? 0),
      costUsd: Number(agg._sum.costUsd ?? 0),
      netUsd: Number(agg._sum.netUsd ?? 0),
    };
  }

  async summary() {
    const DAY = 86400_000;
    const [today, week, month, allTime] = await Promise.all([
      this.windowSum(DAY), this.windowSum(7 * DAY), this.windowSum(30 * DAY),
      this.prisma.revenueLedger.aggregate({ _sum: { grossUsd: true, costUsd: true, netUsd: true } }),
    ]);
    const split = await this.getSplit();
    const totalNet = Number(allTime._sum.netUsd ?? 0);
    const obligations = await this.prisma.payoutRequest.aggregate({
      _sum: { amountUsd: true },
      where: { status: { in: ["pending_review", "approved", "submitted", "processing"] } },
    });
    const grossMarginPct = today.grossUsd > 0 ? (today.netUsd / today.grossUsd) * 100 : 0;
    return {
      today, week, month,
      allTime: { grossUsd: Number(allTime._sum.grossUsd ?? 0), costUsd: Number(allTime._sum.costUsd ?? 0), netUsd: totalNet },
      grossMarginPct: +grossMarginPct.toFixed(2),
      split,
      allocation: {
        providersUsd: +(totalNet * split.providers / 100).toFixed(2),
        treasuryUsd: +(totalNet * split.treasury / 100).toFixed(2),
        referralUsd: +(totalNet * split.referral / 100).toFixed(2),
      },
      payoutObligationsUsd: Number(obligations._sum.amountUsd ?? 0),
    };
  }

  ledger(limit = 100) {
    return this.prisma.revenueLedger.findMany({ orderBy: { createdAt: "desc" }, take: limit });
  }

  /** Aggregate-only, no PII — for the public revenue page. */
  async publicSummary() {
    const s = await this.summary();
    const e = env();
    return {
      revenue30dUsd: s.month.grossUsd,
      net30dUsd: s.month.netUsd,
      allTimeNetUsd: s.allTime.netUsd,
      grossMarginPct: s.grossMarginPct,
      redistribution: s.split,
      allocation: s.allocation,
      // Price oracle — kept in sync with buy.animica.org via ANM_USD_PRICE.
      prices: { anmUsd: Number(e.ANM_USD_PRICE || 0), xmrUsd: Number(e.XMR_USD_PRICE || 0) },
    };
  }
}
