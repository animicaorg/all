import { Injectable } from "@nestjs/common";
import { Prisma } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";

type Entry = "purchase" | "inference_usage" | "rental_usage" | "refund" | "admin_adjustment";

@Injectable()
export class CreditsService {
  constructor(private readonly prisma: PrismaService) {}

  /** Current USD credit balance = latest ledger snapshot (0 if none). */
  async balance(userId: string): Promise<number> {
    const last = await this.prisma.creditLedger.findFirst({
      where: { userId },
      orderBy: { createdAt: "desc" },
      select: { balanceAfterUsd: true },
    });
    return last ? Number(last.balanceAfterUsd) : 0;
  }

  async ledger(userId: string, take = 100) {
    return this.prisma.creditLedger.findMany({
      where: { userId },
      orderBy: { createdAt: "desc" },
      take,
    });
  }

  /** Append a signed ledger entry inside a transaction (atomic balance). */
  async record(
    userId: string,
    type: Entry,
    amountUsd: number,
    opts: { refId?: string; note?: string } = {},
  ) {
    return this.prisma.$transaction(async (tx) => {
      const last = await tx.creditLedger.findFirst({
        where: { userId },
        orderBy: { createdAt: "desc" },
        select: { balanceAfterUsd: true },
      });
      const prev = last ? Number(last.balanceAfterUsd) : 0;
      const next = prev + amountUsd;
      return tx.creditLedger.create({
        data: {
          userId,
          type,
          amountUsd: new Prisma.Decimal(amountUsd),
          balanceAfterUsd: new Prisma.Decimal(next),
          refId: opts.refId,
          note: opts.note,
        },
      });
    });
  }

  /** Deduct usage; returns false (no-op) if balance is insufficient. */
  async deduct(userId: string, amountUsd: number, type: Entry, refId?: string): Promise<boolean> {
    const bal = await this.balance(userId);
    if (bal < amountUsd) return false;
    await this.record(userId, type, -Math.abs(amountUsd), { refId });
    return true;
  }

  async adminAdjust(adminEmail: string, userId: string, amountUsd: number, note?: string) {
    const entry = await this.record(userId, amountUsd >= 0 ? "purchase" : "admin_adjustment", amountUsd, {
      note: note ?? `admin adjust by ${adminEmail}`,
    });
    await auditLog(this.prisma, {
      action: "credits.admin_adjust", entityType: "User", entityId: userId, actor: adminEmail,
      metadata: { amountUsd, note },
    });
    return entry;
  }
}
