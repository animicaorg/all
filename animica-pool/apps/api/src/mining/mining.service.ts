import { Injectable, BadRequestException, NotFoundException, ConflictException } from "@nestjs/common";
import { MiningMode, PayoutAsset } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";
import { getMinerStats, getPoolSummary, connectionString } from "./pool-adapter";
import type { UpsertMiningAccountDto, SetModeDto, SetPayoutDto } from "./mining.dto";

const XMR_ATOMIC = 1e12;

@Injectable()
export class MiningService {
  constructor(private readonly prisma: PrismaService) {}

  async getAccount(userId: string) {
    return this.prisma.miningAccount.findFirst({ where: { userId }, orderBy: { createdAt: "asc" } });
  }

  async upsert(userId: string, dto: UpsertMiningAccountDto) {
    const existingByName = await this.prisma.miningAccount.findUnique({ where: { username: dto.username } });
    if (existingByName && existingByName.userId !== userId) {
      throw new ConflictException("username taken");
    }
    const data = {
      userId,
      username: dto.username,
      workerName: dto.workerName ?? "rig-01",
      mode: dto.mode as MiningMode,
      payoutAsset: dto.payoutAsset as PayoutAsset,
      anmAddress: dto.anmAddress,
      xmrAddress: dto.xmrAddress ?? null,
      payoutAddress: dto.payoutAddress ?? null,
    };
    const account = existingByName
      ? await this.prisma.miningAccount.update({ where: { id: existingByName.id }, data })
      : await this.prisma.miningAccount.create({ data });
    await auditLog(this.prisma, { action: "mining.account.upsert", entityType: "MiningAccount", entityId: account.id, actor: userId, metadata: { mode: dto.mode } });
    return account;
  }

  private async requireAccount(userId: string) {
    const account = await this.getAccount(userId);
    if (!account) throw new NotFoundException("No mining account — create one first");
    return account;
  }

  async setMode(userId: string, dto: SetModeDto) {
    const account = await this.requireAccount(userId);
    return this.prisma.miningAccount.update({ where: { id: account.id }, data: { mode: dto.mode as MiningMode } });
  }

  async setPayout(userId: string, dto: SetPayoutDto) {
    const account = await this.requireAccount(userId);
    if (dto.payoutAsset !== "ANM" && dto.payoutAsset !== "ORIGINAL_ASSET" && dto.payoutAsset !== "SPLIT_ANM_XMR" && !dto.payoutAddress) {
      throw new BadRequestException("payoutAddress required for this asset");
    }
    return this.prisma.miningAccount.update({
      where: { id: account.id },
      data: { payoutAsset: dto.payoutAsset as PayoutAsset, payoutAddress: dto.payoutAddress ?? account.payoutAddress },
    });
  }

  async stats(userId: string) {
    const account = await this.requireAccount(userId);
    const e = env();
    const stats = await getMinerStats(account.anmAddress ?? "");
    const anmUsd = Number(e.ANM_USD_PRICE || 0);
    const xmrUsd = Number(e.XMR_USD_PRICE || 0);
    const estimatedEarningsUsd =
      stats.pendingBalanceAnm * anmUsd + (stats.xmrOwedAtomic / XMR_ATOMIC) * xmrUsd;
    const conn = connectionString(account.mode, account.anmAddress ?? "", account.workerName ?? "rig-01");

    // Snapshot for history (best-effort).
    await this.prisma.miningStatsSnapshot.create({
      data: {
        miningAccountId: account.id,
        hashrate: stats.hashrate, hashrate24h: stats.hashrate24h,
        acceptedShares: stats.acceptedShares, rejectedShares: stats.rejectedShares,
        pendingBalance: stats.pendingBalanceAnm, paidBalance: stats.paidBalanceAnm,
        contributionPct: stats.contributionPct,
      },
    }).catch(() => {});

    return {
      account: {
        username: account.username, workerName: account.workerName, mode: account.mode,
        payoutAsset: account.payoutAsset, payoutAddress: account.payoutAddress,
        anmAddress: account.anmAddress, xmrAddress: account.xmrAddress,
      },
      connection: conn,
      poolFeePercent: Number(e.POOL_FEE_PERCENT || 0),
      stats,
      estimatedEarningsUsd,
      prices: { anmUsd, xmrUsd },
    };
  }

  async payouts(userId: string) {
    const account = await this.requireAccount(userId);
    // Real on-chain payouts arrive via the payout system (P8). For now expose
    // the pool-reported paid balance + any platform payout requests.
    const stats = await getMinerStats(account.anmAddress ?? "");
    const requests = await this.prisma.payoutRequest.findMany({
      where: { userId, type: "mining" },
      orderBy: { createdAt: "desc" },
      take: 50,
    });
    return { paidBalanceAnm: stats.paidBalanceAnm, xmrPaidAtomic: stats.xmrPaidAtomic, requests };
  }

  async poolSummary() {
    return getPoolSummary();
  }
}
