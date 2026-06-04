import { Injectable } from "@nestjs/common";
import { Prisma, PayoutAsset } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { env } from "../config/env";

@Injectable()
export class ReferralService {
  constructor(private readonly prisma: PrismaService) {}

  /** On a completed credit purchase by a referred user, accrue a reward for
   *  the referrer (% of the purchase). Idempotency is handled upstream (the
   *  webhook credits a purchase at most once). */
  async onPurchase(buyerUserId: string, amountUsd: number) {
    const buyer = await this.prisma.user.findUnique({ where: { id: buyerUserId } });
    if (!buyer?.referredById) return;
    const pct = env().SPLIT_REFERRAL_PERCENT; // referral reward % of purchase
    const rewardUsd = +(amountUsd * pct / 100).toFixed(8);
    if (rewardUsd <= 0) return;

    const existing = await this.prisma.referral.findFirst({ where: { referrerUserId: buyer.referredById, referredUserId: buyer.id } });
    const referrer = await this.prisma.user.findUnique({ where: { id: buyer.referredById }, select: { referralCode: true } });
    if (existing) {
      await this.prisma.referral.update({
        where: { id: existing.id },
        data: {
          purchaseAmountUsd: new Prisma.Decimal(Number(existing.purchaseAmountUsd) + amountUsd),
          rewardUsd: new Prisma.Decimal(Number(existing.rewardUsd) + rewardUsd),
        },
      });
    } else {
      await this.prisma.referral.create({
        data: {
          referrerUserId: buyer.referredById, referredUserId: buyer.id,
          referralCode: referrer?.referralCode ?? "",
          purchaseAmountUsd: new Prisma.Decimal(amountUsd), rewardUsd: new Prisma.Decimal(rewardUsd),
          rewardAsset: "USDT" as PayoutAsset, status: "pending",
        },
      });
    }
  }

  async dashboard(userId: string) {
    const user = await this.prisma.user.findUnique({ where: { id: userId }, select: { referralCode: true } });
    const [signups, referrals] = await Promise.all([
      this.prisma.user.count({ where: { referredById: userId } }),
      this.prisma.referral.findMany({ where: { referrerUserId: userId }, orderBy: { createdAt: "desc" } }),
    ]);
    const pendingUsd = referrals.filter((r) => r.status === "pending" || r.status === "approved").reduce((s, r) => s + Number(r.rewardUsd), 0);
    const paidUsd = referrals.filter((r) => r.status === "paid").reduce((s, r) => s + Number(r.rewardUsd), 0);
    const base = env().NEXT_PUBLIC_APP_URL.replace(/\/$/, "");
    return {
      code: user?.referralCode ?? null,
      link: user?.referralCode ? `${base}/register?ref=${user.referralCode}` : null,
      signups, referrals, pendingUsd: +pendingUsd.toFixed(2), paidUsd: +paidUsd.toFixed(2),
    };
  }
}
