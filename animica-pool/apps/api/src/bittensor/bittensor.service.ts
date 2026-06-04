import { Injectable, BadRequestException, NotFoundException, UnauthorizedException, Logger, type OnModuleInit } from "@nestjs/common";
import { Prisma, BittensorExecutorStatus, type BittensorMiner, type Worker } from "@prisma/client";
import { createHash } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { RevenueService } from "../revenue/revenue.service";
import { auditLog } from "../common/audit";
import { env } from "../config/env";
import { taoUsd } from "./tao-price";
import { provisionScript } from "./provision";

// Worker agent heartbeats every 30s → max beats/day for the uptime gate.
const BEATS_PER_DAY = 2880;
const DAY_MS = 86_400_000;
// Ignore alpha deltas below dust (rounding noise between polls).
const ALPHA_DUST = 1e-6;

function utcDay(d = new Date()): Date {
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
}

interface EarningInput {
  executorId?: string;
  kind: "emission" | "rental" | "adjustment";
  taoAmount?: number;
  alphaAmount?: number;
  usdValue?: number;
  periodStart: Date;
  periodEnd: Date;
  raw?: unknown;
}

@Injectable()
export class BittensorService implements OnModuleInit {
  private readonly log = new Logger("Bittensor");
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly prisma: PrismaService, private readonly revenue: RevenueService) {}

  onModuleInit() {
    const e = env();
    const everyMs = Math.max(5, e.BITTENSOR_POLL_INTERVAL_MIN) * 60_000;
    // Always schedule — pollOnce() self-skips while unconfigured (no taostats
    // key / PENDING hotkey), so the poller activates the moment the miner
    // registers without a restart.
    this.pollTimer = setInterval(() => {
      this.pollOnce("interval").catch((err) => this.log.warn(`poll failed: ${err}`));
    }, everyMs);
    this.pollTimer.unref?.();
  }

  // ---- Flags (Setting override → env, same pattern as the revenue split) ----
  async miningEnabled(): Promise<boolean> {
    const row = await this.prisma.setting.findUnique({ where: { key: "BITTENSOR_MINING_ENABLED" } });
    if (row) return row.value === "true";
    return env().BITTENSOR_MINING_ENABLED;
  }

  async setMiningEnabled(adminEmail: string, enabled: boolean) {
    await this.prisma.setting.upsert({
      where: { key: "BITTENSOR_MINING_ENABLED" },
      create: { key: "BITTENSOR_MINING_ENABLED", value: String(enabled) },
      update: { value: String(enabled) },
    });
    await auditLog(this.prisma, { action: "bittensor.flag", entityType: "Setting", actor: adminEmail, metadata: { miningEnabled: enabled } });
    return { miningEnabled: enabled };
  }

  /** The pool's miner identity for the configured netuid. Auto-creates a
   *  `planned` row from env so enrollment can queue rigs before the
   *  registration burn is funded (zero-upfront-capital rollout). */
  private async defaultMiner() {
    const e = env();
    const existing = await this.prisma.bittensorMiner.findFirst({
      where: { netuid: e.BITTENSOR_MINING_NETUID },
      orderBy: { createdAt: "asc" },
    });
    if (existing) return existing;
    return this.prisma.bittensorMiner.create({
      data: {
        netuid: e.BITTENSOR_MINING_NETUID,
        hotkeySs58: e.BITTENSOR_MINER_HOTKEY || `PENDING-netuid-${e.BITTENSOR_MINING_NETUID}`,
        status: "planned",
        notes: "auto-created; set the real hotkey after `btcli subnet register`",
      },
    });
  }

  // ---- Treasury accumulator (funds the registration burn from Track 1 margin) ----
  async treasuryProgress() {
    const e = env();
    const [agg, split] = await Promise.all([
      this.prisma.revenueLedger.aggregate({ _sum: { netUsd: true } }),
      this.revenue.getSplit(),
    ]);
    const netUsd = Number(agg._sum.netUsd ?? 0);
    const accruedUsd = +(netUsd * split.treasury / 100).toFixed(2);
    const targetUsd = e.BITTENSOR_REG_TARGET_USD;
    return {
      targetUsd,
      accruedUsd,
      pct: targetUsd > 0 ? +Math.min(100, (accruedUsd / targetUsd) * 100).toFixed(2) : 100,
      funded: accruedUsd >= targetUsd,
      note: "treasury slice of net revenue earmarked for the SN51 UID registration burn + first executor collateral",
    };
  }

  // ---- Public overview (positioning page / dashboards) ----
  async overview() {
    const e = env();
    const [enabled, miner, counts, earningsAgg, treasury, poller] = await Promise.all([
      this.miningEnabled(),
      this.defaultMiner(),
      this.prisma.bittensorExecutor.groupBy({ by: ["status"], _count: { _all: true } }),
      this.prisma.bittensorEarning.aggregate({ _sum: { usdValue: true, ownerShareUsd: true, taoAmount: true } }),
      this.treasuryProgress(),
      this.pollStatus(),
    ]);
    const executors: Record<string, number> = {};
    for (const c of counts) executors[c.status] = c._count._all;
    return {
      demandSide: { provider: "chutes-sn64", enabled: env().BITTENSOR_ENABLED },
      supplySide: {
        netuid: e.BITTENSOR_MINING_NETUID,
        miningEnabled: enabled,
        minerStatus: miner.status,
        hotkeySet: !miner.hotkeySs58.startsWith("PENDING"),
        executors,
        ownerSharePercent: e.BITTENSOR_OWNER_SHARE_PERCENT,
        holdbackDays: e.BITTENSOR_HOLDBACK_DAYS,
        eligibility: {
          minUptimePct: e.BITTENSOR_MIN_UPTIME_PCT,
          minHistoryDays: e.BITTENSOR_MIN_HISTORY_DAYS,
          minVramGb: e.BITTENSOR_MIN_VRAM_GB,
        },
      },
      earnings: {
        totalUsd: Number(earningsAgg._sum.usdValue ?? 0),
        ownerPaidUsd: Number(earningsAgg._sum.ownerShareUsd ?? 0),
        totalTao: Number(earningsAgg._sum.taoAmount ?? 0),
      },
      treasury,
      poller,
    };
  }

  // ---- Eligibility (slashing protection: only proven rigs enroll) ----
  /** SN51 executors are CUDA containers on NVIDIA drivers — Apple (Metal),
   *  AMD and Intel GPUs can't serve them, however much memory they have. */
  private isCudaGpu(model?: string | null): boolean {
    if (!model) return false;
    if (/apple|radeon|\bamd\b|intel|\barc\b/i.test(model)) return false;
    return /nvidia|geforce|rtx|gtx|tesla|quadro|titan|\b[ahlvb]\d{2,3}\b/i.test(model);
  }

  async eligibility(userId: string, workerId: string) {
    const worker = await this.prisma.worker.findUnique({ where: { id: workerId } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    return this.eligibilityForWorker(worker);
  }

  private async eligibilityForWorker(worker: Worker) {
    const workerId = worker.id;
    const e = env();
    const since = utcDay(new Date(Date.now() - e.BITTENSOR_MIN_HISTORY_DAYS * DAY_MS));
    const days = await this.prisma.workerHeartbeatDay.findMany({
      where: { workerId, day: { gte: since } },
    });
    const historyDays = days.length;
    const beats = days.reduce((s, d) => s + d.beats, 0);
    const uptimePct = historyDays > 0
      ? +Math.min(100, (beats / (e.BITTENSOR_MIN_HISTORY_DAYS * BEATS_PER_DAY)) * 100).toFixed(2)
      : 0;
    // hasGpu deliberately means "SN51-eligible (NVIDIA CUDA) GPU": older CLIs
    // only render this fixed check set, so a detected-but-unusable GPU (e.g.
    // Apple Silicon) must fail here — not in a key they'd never display.
    const checks = {
      hasGpu: this.isCudaGpu(worker.gpuModel),
      vramOk: (worker.vramGb ?? 0) >= e.BITTENSOR_MIN_VRAM_GB,
      historyOk: historyDays >= e.BITTENSOR_MIN_HISTORY_DAYS,
      uptimeOk: uptimePct >= e.BITTENSOR_MIN_UPTIME_PCT,
      notBanned: worker.status !== "banned" && worker.status !== "disabled",
    };
    const gpuNote = worker.gpuModel && !checks.hasGpu
      ? `${worker.gpuModel} can't back SN51 — executors need an NVIDIA CUDA GPU. This rig can still earn via inference jobs, rentals and mining.`
      : null;
    return {
      workerId,
      eligible: Object.values(checks).every(Boolean),
      uptimePct,
      historyDays,
      gpuModel: worker.gpuModel,
      vramGb: worker.vramGb,
      gpuNote,
      checks,
      thresholds: {
        minUptimePct: e.BITTENSOR_MIN_UPTIME_PCT,
        minHistoryDays: e.BITTENSOR_MIN_HISTORY_DAYS,
        minVramGb: e.BITTENSOR_MIN_VRAM_GB,
      },
    };
  }

  // ---- Enrollment (session user or worker token) ----
  async enroll(userId: string, dto: { workerId: string; gpuType?: string; gpuCount?: number; pricePerHourUsd?: number }) {
    const worker = await this.prisma.worker.findUnique({ where: { id: dto.workerId } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    return this.enrollWorker(worker, dto, userId);
  }

  private async enrollWorker(worker: Worker, dto: { gpuType?: string; gpuCount?: number; pricePerHourUsd?: number }, actor: string) {
    const elig = await this.eligibilityForWorker(worker);
    if (!elig.eligible) {
      throw new BadRequestException({ message: "Worker not eligible for SN51 enrollment", eligibility: elig });
    }
    const existing = await this.prisma.bittensorExecutor.findUnique({ where: { workerId: worker.id } });
    if (existing && existing.status !== "retired") throw new BadRequestException("Worker already enrolled");
    const miner = await this.defaultMiner();
    const data = {
      minerId: miner.id,
      workerId: worker.id,
      gpuType: dto.gpuType || worker.gpuModel || "unknown",
      gpuCount: dto.gpuCount ?? 1,
      pricePerHourUsd: dto.pricePerHourUsd != null ? new Prisma.Decimal(dto.pricePerHourUsd) : null,
      status: "pending" as BittensorExecutorStatus,
    };
    const executor = existing
      ? await this.prisma.bittensorExecutor.update({ where: { id: existing.id }, data })
      : await this.prisma.bittensorExecutor.create({ data });
    await auditLog(this.prisma, { action: "bittensor.enroll", entityType: "BittensorExecutor", entityId: executor.id, actor, metadata: { workerId: worker.id, gpuType: data.gpuType } });
    return { executor, eligibility: elig, miningLive: await this.miningEnabled() };
  }

  async myExecutors(userId: string) {
    const e = env();
    const executors = await this.prisma.bittensorExecutor.findMany({
      where: { worker: { userId } },
      include: { miner: { select: { netuid: true, status: true } } },
      orderBy: { enrolledAt: "desc" },
    });
    const cutoff = new Date(Date.now() - e.BITTENSOR_HOLDBACK_DAYS * DAY_MS);
    const result = [] as Array<Record<string, unknown>>;
    for (const ex of executors) {
      const [released, held] = await Promise.all([
        this.prisma.bittensorEarning.aggregate({
          _sum: { ownerShareUsd: true },
          where: { executorId: ex.id, forfeited: false, createdAt: { lte: cutoff } },
        }),
        this.prisma.bittensorEarning.aggregate({
          _sum: { ownerShareUsd: true },
          where: { executorId: ex.id, forfeited: false, createdAt: { gt: cutoff } },
        }),
      ]);
      result.push({
        ...ex,
        earnings: {
          releasedUsd: Number(released._sum.ownerShareUsd ?? 0),
          heldUsd: Number(held._sum.ownerShareUsd ?? 0),
          holdbackDays: e.BITTENSOR_HOLDBACK_DAYS,
        },
      });
    }
    return result;
  }

  async provision(userId: string, executorId: string) {
    const executor = await this.prisma.bittensorExecutor.findUnique({
      where: { id: executorId },
      include: { worker: true, miner: true },
    });
    if (!executor || executor.worker.userId !== userId) throw new NotFoundException("Executor not found");
    return this.provisionForExecutor(executor);
  }

  private async provisionForExecutor(executor: { id: string; externalPort: number; sshPort: number; miner: BittensorMiner }) {
    const hotkey = executor.miner.hotkeySs58;
    if (hotkey.startsWith("PENDING")) {
      throw new BadRequestException(
        "Pool miner hotkey not yet registered on-chain — enrollment is queued; provisioning opens when the treasury funds the SN51 registration burn.",
      );
    }
    const script = provisionScript({
      minerHotkey: hotkey,
      externalPort: executor.externalPort,
      sshPort: executor.sshPort,
      rentingPortRange: "2000-2005",
      executorRef: executor.id,
      paused: !(await this.miningEnabled()),
    });
    await this.prisma.bittensorExecutor.update({ where: { id: executor.id }, data: { status: "provisioning" } });
    return { script };
  }

  // ---- Machine (worker token) — executor container status via heartbeat ----
  private async workerFromToken(authHeader?: string) {
    const raw = authHeader?.startsWith("Bearer ") ? authHeader.slice(7).trim() : "";
    if (!raw) throw new UnauthorizedException("Missing worker token");
    const tokenHash = createHash("sha256").update(raw).digest("hex");
    const worker = await this.prisma.worker.findUnique({ where: { tokenHash } });
    if (!worker || worker.status === "banned" || worker.status === "disabled") {
      throw new UnauthorizedException("Invalid or disabled worker token");
    }
    return worker;
  }

  async reportExecutor(authHeader: string | undefined, body: { running: boolean; executorUuid?: string }) {
    const worker = await this.workerFromToken(authHeader);
    const executor = await this.prisma.bittensorExecutor.findUnique({
      where: { workerId: worker.id },
      include: { miner: true },
    });
    if (!executor) return { ok: false, enrolled: false };
    const minerLive = executor.miner.status === "registered" || executor.miner.status === "active";
    let status = executor.status;
    if (body.running) {
      status = minerLive ? "active" : "provisioning";
    } else if (executor.status === "active") {
      status = "offline";
    }
    await this.prisma.bittensorExecutor.update({
      where: { id: executor.id },
      data: {
        containerOk: body.running,
        status,
        executorUuid: body.executorUuid ?? executor.executorUuid,
        lastVerifiedAt: body.running ? new Date() : executor.lastVerifiedAt,
      },
    });
    return { ok: true, enrolled: true, status };
  }

  /** Worker-token self-service (powers the `animica bittensor` CLI): the rig
   *  checks its own status, enrolls itself, and fetches its provisioning
   *  script with nothing but the worker token already on the machine. */
  async executorMe(authHeader: string | undefined) {
    const worker = await this.workerFromToken(authHeader);
    const [executor, elig, miningLive] = await Promise.all([
      this.prisma.bittensorExecutor.findUnique({
        where: { workerId: worker.id },
        include: { miner: { select: { netuid: true, status: true, hotkeySs58: true } } },
      }),
      this.eligibilityForWorker(worker),
      this.miningEnabled(),
    ]);
    return {
      workerId: worker.id,
      enrolled: !!executor && executor.status !== "retired",
      executor: executor
        ? { id: executor.id, status: executor.status, gpuType: executor.gpuType, gpuCount: executor.gpuCount, netuid: executor.miner.netuid, minerStatus: executor.miner.status, containerOk: executor.containerOk }
        : null,
      eligibility: elig,
      miningLive,
    };
  }

  async executorEnroll(authHeader: string | undefined, dto: { gpuType?: string; gpuCount?: number; pricePerHourUsd?: number }) {
    const worker = await this.workerFromToken(authHeader);
    return this.enrollWorker(worker, dto ?? {}, `worker:${worker.id}`);
  }

  async executorProvision(authHeader: string | undefined) {
    const worker = await this.workerFromToken(authHeader);
    const executor = await this.prisma.bittensorExecutor.findUnique({
      where: { workerId: worker.id },
      include: { miner: true },
    });
    if (!executor || executor.status === "retired") throw new BadRequestException("Worker not enrolled — run enroll first");
    return this.provisionForExecutor(executor);
  }

  // ---- Admin ----
  async adminSetMiner(adminEmail: string, dto: { netuid?: number; hotkeySs58: string; status?: string; registrationBurnTao?: number; notes?: string }) {
    const e = env();
    const netuid = dto.netuid ?? e.BITTENSOR_MINING_NETUID;
    // Replace the auto-created PENDING placeholder when the real hotkey lands.
    const placeholder = await this.prisma.bittensorMiner.findFirst({
      where: { netuid, hotkeySs58: { startsWith: "PENDING" } },
    });
    const data = {
      netuid,
      hotkeySs58: dto.hotkeySs58,
      status: dto.status ?? "registered",
      registrationBurnTao: dto.registrationBurnTao != null ? new Prisma.Decimal(dto.registrationBurnTao) : undefined,
      registeredAt: dto.status === "planned" ? undefined : new Date(),
      notes: dto.notes,
    };
    const miner = placeholder
      ? await this.prisma.bittensorMiner.update({ where: { id: placeholder.id }, data })
      : await this.prisma.bittensorMiner.upsert({
          where: { hotkeySs58: dto.hotkeySs58 },
          create: data,
          update: { status: data.status, registrationBurnTao: data.registrationBurnTao, notes: data.notes },
        });
    await auditLog(this.prisma, { action: "bittensor.miner.set", entityType: "BittensorMiner", entityId: miner.id, actor: adminEmail, metadata: { netuid, status: miner.status } });
    return miner;
  }

  adminListMiners() {
    return this.prisma.bittensorMiner.findMany({ include: { _count: { select: { executors: true } } }, orderBy: { createdAt: "asc" } });
  }

  adminListExecutors() {
    return this.prisma.bittensorExecutor.findMany({
      include: { worker: { select: { name: true, userId: true, gpuModel: true, lastSeenAt: true } }, miner: { select: { netuid: true } } },
      orderBy: { enrolledAt: "desc" },
    });
  }

  async adminUpdateExecutor(adminEmail: string, id: string, dto: { status?: string; executorUuid?: string; collateralTao?: number; pricePerHourUsd?: number }) {
    const executor = await this.prisma.bittensorExecutor.update({
      where: { id },
      data: {
        status: dto.status as BittensorExecutorStatus | undefined,
        executorUuid: dto.executorUuid,
        collateralTao: dto.collateralTao != null ? new Prisma.Decimal(dto.collateralTao) : undefined,
        pricePerHourUsd: dto.pricePerHourUsd != null ? new Prisma.Decimal(dto.pricePerHourUsd) : undefined,
      },
    });
    await auditLog(this.prisma, { action: "bittensor.executor.update", entityType: "BittensorExecutor", entityId: id, actor: adminEmail, metadata: dto as object });
    return executor;
  }

  /** Record a TAO/alpha earnings batch (emission for the miner, or rental for
   *  one executor), convert to USD, split owner/pool shares, and feed the
   *  revenue ledger: gross = USD value, cost = owner shares (what we owe rig
   *  owners), net = pool share → flows into the treasury accumulator that
   *  funds further registrations. */
  async adminRecordEarning(adminEmail: string, dto: {
    minerId?: string; executorId?: string; kind: "emission" | "rental" | "adjustment";
    taoAmount?: number; alphaAmount?: number; usdValue?: number;
    periodStart: string; periodEnd: string; raw?: unknown;
  }) {
    const miner = dto.minerId
      ? await this.prisma.bittensorMiner.findUnique({ where: { id: dto.minerId } })
      : await this.defaultMiner();
    if (!miner) throw new NotFoundException("Miner not found");
    return this.recordEarningCore(miner, {
      ...dto,
      periodStart: new Date(dto.periodStart),
      periodEnd: new Date(dto.periodEnd),
    }, adminEmail);
  }

  /** Shared earnings path (admin endpoint + on-chain poller): convert to USD,
   *  split owner/pool, persist rows, feed the revenue ledger. */
  private async recordEarningCore(miner: BittensorMiner, dto: EarningInput, actor: string) {
    const tao = dto.taoAmount ?? 0;
    const usd = dto.usdValue ?? (tao > 0 ? +(tao * (await taoUsd())).toFixed(8) : 0);
    if (!(usd > 0)) throw new BadRequestException("Earning must have a positive usdValue or taoAmount");
    const ownerPct = env().BITTENSOR_OWNER_SHARE_PERCENT;
    const period = { periodStart: dto.periodStart, periodEnd: dto.periodEnd };

    const rows: Prisma.BittensorEarningCreateManyInput[] = [];
    if (dto.executorId) {
      const ownerShare = +(usd * ownerPct / 100).toFixed(8);
      rows.push({
        minerId: miner.id, executorId: dto.executorId, kind: dto.kind, ...period,
        taoAmount: new Prisma.Decimal(tao), alphaAmount: new Prisma.Decimal(dto.alphaAmount ?? 0),
        usdValue: new Prisma.Decimal(usd),
        ownerShareUsd: new Prisma.Decimal(ownerShare),
        poolShareUsd: new Prisma.Decimal(+(usd - ownerShare).toFixed(8)),
        raw: (dto.raw ?? undefined) as Prisma.InputJsonValue | undefined,
      });
    } else {
      // Emission batch: split across active executors weighted by gpuCount.
      const active = await this.prisma.bittensorExecutor.findMany({ where: { minerId: miner.id, status: "active" } });
      const totalWeight = active.reduce((s, x) => s + x.gpuCount, 0);
      if (totalWeight === 0) {
        // No active rigs (e.g. bootstrap emissions) — all pool.
        rows.push({
          minerId: miner.id, kind: dto.kind, ...period,
          taoAmount: new Prisma.Decimal(tao), alphaAmount: new Prisma.Decimal(dto.alphaAmount ?? 0),
          usdValue: new Prisma.Decimal(usd), ownerShareUsd: new Prisma.Decimal(0),
          poolShareUsd: new Prisma.Decimal(usd),
          raw: (dto.raw ?? undefined) as Prisma.InputJsonValue | undefined,
        });
      } else {
        for (const ex of active) {
          const slice = usd * (ex.gpuCount / totalWeight);
          const ownerShare = +(slice * ownerPct / 100).toFixed(8);
          rows.push({
            minerId: miner.id, executorId: ex.id, kind: dto.kind, ...period,
            taoAmount: new Prisma.Decimal(+(tao * (ex.gpuCount / totalWeight)).toFixed(9)),
            alphaAmount: new Prisma.Decimal(+((dto.alphaAmount ?? 0) * (ex.gpuCount / totalWeight)).toFixed(9)),
            usdValue: new Prisma.Decimal(+slice.toFixed(8)),
            ownerShareUsd: new Prisma.Decimal(ownerShare),
            poolShareUsd: new Prisma.Decimal(+(slice - ownerShare).toFixed(8)),
          });
        }
      }
    }
    await this.prisma.bittensorEarning.createMany({ data: rows });
    const ownerTotal = rows.reduce((s, r) => s + Number(r.ownerShareUsd ?? 0), 0);
    await this.revenue.record({ sourceType: "bittensor", sourceId: miner.id, grossUsd: usd, costUsd: ownerTotal });
    await auditLog(this.prisma, { action: "bittensor.earning.record", entityType: "BittensorMiner", entityId: miner.id, actor, metadata: { kind: dto.kind, usd, tao, rows: rows.length } });
    return { recorded: rows.length, usdValue: usd, ownerShareUsd: +ownerTotal.toFixed(8), poolShareUsd: +(usd - ownerTotal).toFixed(8) };
  }

  // ---- On-chain earnings poller (taostats) ----
  // Emission accounting by balance delta: emissions accrue as alpha staked to
  // the miner hotkey, so Δ(alpha stake) between polls = alpha earned. USD =
  // Δalpha × alpha price (TAO, from the subnet AMM pool) × TAO/USD.
  private async taostats(path: string): Promise<any> {
    const e = env();
    const res = await fetch(`${e.TAOSTATS_API_URL.replace(/\/$/, "")}${path}`, {
      headers: { accept: "application/json", authorization: e.TAOSTATS_API_KEY },
    });
    if (!res.ok) throw new Error(`taostats ${path} → HTTP ${res.status}: ${(await res.text()).slice(0, 160)}`);
    return res.json();
  }

  /** taostats responses are paginated `{ data: [...] }`; field names have
   *  shifted across versions — parse defensively and keep the raw payload in
   *  the snapshot for forensics. Chain amounts are RAO-denominated (1e9). */
  private parseAlphaStake(json: any): number | null {
    const row = json?.data?.[0];
    if (!row) return null;
    const cand = row.balance ?? row.alpha_balance ?? row.stake ?? row.amount;
    const n = Number(cand);
    if (!Number.isFinite(n)) return null;
    return n > 1e6 ? n / 1e9 : n; // heuristics: RAO vs already-converted units
  }

  private parseAlphaPriceTao(json: any): number | null {
    const row = json?.data?.[0];
    if (!row) return null;
    const cand = row.price ?? row.alpha_price ?? row.alpha_price_in_tao;
    const n = Number(cand);
    return Number.isFinite(n) && n > 0 ? n : null;
  }

  async pollStatus() {
    const row = await this.prisma.setting.findUnique({ where: { key: "BITTENSOR_LAST_POLL" } });
    const e = env();
    return {
      configured: !!e.TAOSTATS_API_KEY,
      intervalMin: e.BITTENSOR_POLL_INTERVAL_MIN,
      last: row ? JSON.parse(row.value) : null,
    };
  }

  private async savePollStatus(status: Record<string, unknown>) {
    const value = JSON.stringify({ ...status, at: new Date().toISOString() });
    await this.prisma.setting.upsert({
      where: { key: "BITTENSOR_LAST_POLL" },
      create: { key: "BITTENSOR_LAST_POLL", value },
      update: { value },
    }).catch(() => {});
  }

  async pollOnce(trigger: "interval" | "manual") {
    const e = env();
    if (!e.TAOSTATS_API_KEY) {
      const res = { ok: false, trigger, skipped: "TAOSTATS_API_KEY not set" };
      if (trigger === "manual") await this.savePollStatus(res);
      return res;
    }
    const miner = await this.defaultMiner();
    if (miner.hotkeySs58.startsWith("PENDING") || miner.status === "planned" || miner.status === "deregistered") {
      const res = { ok: false, trigger, skipped: `miner ${miner.status} (hotkey ${miner.hotkeySs58.startsWith("PENDING") ? "unset" : "set"})` };
      if (trigger === "manual") await this.savePollStatus(res);
      return res;
    }

    const netuid = miner.netuid;
    const [stakeJson, poolJson, taoPrice] = await Promise.all([
      this.taostats(`/api/dtao/stake_balance/latest/v1?hotkey=${encodeURIComponent(miner.hotkeySs58)}&netuid=${netuid}`),
      this.taostats(`/api/dtao/pool/latest/v1?netuid=${netuid}`),
      taoUsd(),
    ]);
    const alphaStake = this.parseAlphaStake(stakeJson);
    const alphaPriceTao = this.parseAlphaPriceTao(poolJson);
    if (alphaStake == null) {
      const res = { ok: false, trigger, error: "could not parse alpha stake from taostats — see snapshot raw" };
      await this.prisma.bittensorMinerSnapshot.create({
        data: { minerId: miner.id, alphaStake: new Prisma.Decimal(0), raw: { stake: stakeJson, pool: poolJson } as Prisma.InputJsonValue },
      }).catch(() => {});
      await this.savePollStatus(res);
      return res;
    }

    const prev = await this.prisma.bittensorMinerSnapshot.findFirst({
      where: { minerId: miner.id, alphaStake: { gt: 0 } },
      orderBy: { createdAt: "desc" },
    });
    const snapshot = await this.prisma.bittensorMinerSnapshot.create({
      data: {
        minerId: miner.id,
        alphaStake: new Prisma.Decimal(alphaStake),
        alphaPriceTao: new Prisma.Decimal(alphaPriceTao ?? 0),
        taoPriceUsd: new Prisma.Decimal(taoPrice),
        raw: { stake: stakeJson?.data?.[0] ?? null, pool: poolJson?.data?.[0] ?? null } as Prisma.InputJsonValue,
      },
    });

    let recorded: Awaited<ReturnType<BittensorService["recordEarningCore"]>> | null = null;
    const deltaAlpha = prev ? alphaStake - Number(prev.alphaStake) : 0;
    if (prev && deltaAlpha > ALPHA_DUST && alphaPriceTao) {
      const deltaTao = +(deltaAlpha * alphaPriceTao).toFixed(9);
      recorded = await this.recordEarningCore(miner, {
        kind: "emission",
        alphaAmount: +deltaAlpha.toFixed(9),
        taoAmount: deltaTao,
        usdValue: +(deltaTao * taoPrice).toFixed(8),
        periodStart: prev.createdAt,
        periodEnd: snapshot.createdAt,
        raw: { poll: true, deltaAlpha, alphaPriceTao, taoPrice },
      }, `poll:${trigger}`);
      this.log.log(`poll: +${deltaAlpha.toFixed(6)} α (~$${recorded.usdValue}) recorded`);
    } else if (prev && deltaAlpha < -ALPHA_DUST) {
      // Stake decreased — an operator withdrawal, not negative earnings.
      this.log.warn(`poll: alpha stake decreased by ${(-deltaAlpha).toFixed(6)} (withdrawal?) — no earning recorded`);
    }

    const res = {
      ok: true, trigger, netuid,
      alphaStake, deltaAlpha: +deltaAlpha.toFixed(9),
      alphaPriceTao, taoPriceUsd: taoPrice,
      recordedUsd: recorded?.usdValue ?? 0,
      firstSnapshot: !prev,
    };
    await this.savePollStatus(res);
    return res;
  }

  /** Recent earnings, aggregate-only (public page). */
  async recentEarnings(limit = 30) {
    const rows = await this.prisma.bittensorEarning.findMany({
      orderBy: { createdAt: "desc" },
      take: Math.min(limit, 100),
      select: {
        id: true, kind: true, periodStart: true, periodEnd: true,
        alphaAmount: true, taoAmount: true, usdValue: true,
        ownerShareUsd: true, poolShareUsd: true, createdAt: true,
      },
    });
    return rows.map((r) => ({
      ...r,
      alphaAmount: Number(r.alphaAmount), taoAmount: Number(r.taoAmount), usdValue: Number(r.usdValue),
      ownerShareUsd: Number(r.ownerShareUsd), poolShareUsd: Number(r.poolShareUsd),
    }));
  }

  /** A collateral slash happened for this executor: forfeit the owner's
   *  held-back (unreleased) earnings to absorb it, suspend the rig. Forfeits
   *  whole rows newest-first until the slash is covered; any uncovered
   *  remainder is the pool's loss and is reported back. */
  async adminSlash(adminEmail: string, executorId: string, dto: { usdValue?: number; taoAmount?: number; note?: string }) {
    const executor = await this.prisma.bittensorExecutor.findUnique({ where: { id: executorId } });
    if (!executor) throw new NotFoundException("Executor not found");
    const slashUsd = dto.usdValue ?? (dto.taoAmount ? +(dto.taoAmount * (await taoUsd())).toFixed(8) : 0);
    if (!(slashUsd > 0)) throw new BadRequestException("Slash must have a positive usdValue or taoAmount");
    const cutoff = new Date(Date.now() - env().BITTENSOR_HOLDBACK_DAYS * DAY_MS);
    const held = await this.prisma.bittensorEarning.findMany({
      where: { executorId, forfeited: false, createdAt: { gt: cutoff } },
      orderBy: { createdAt: "desc" },
    });
    let covered = 0;
    const forfeit: string[] = [];
    for (const row of held) {
      if (covered >= slashUsd) break;
      covered += Number(row.ownerShareUsd);
      forfeit.push(row.id);
    }
    if (forfeit.length) {
      await this.prisma.bittensorEarning.updateMany({ where: { id: { in: forfeit } }, data: { forfeited: true } });
    }
    await this.prisma.bittensorExecutor.update({
      where: { id: executorId },
      data: { status: "suspended", slashCount: { increment: 1 } },
    });
    await auditLog(this.prisma, { action: "bittensor.executor.slash", entityType: "BittensorExecutor", entityId: executorId, actor: adminEmail, metadata: { slashUsd, coveredUsd: covered, forfeitedRows: forfeit.length, note: dto.note } });
    return {
      slashUsd,
      coveredByHoldbackUsd: +Math.min(covered, slashUsd).toFixed(8),
      poolLossUsd: +Math.max(0, slashUsd - covered).toFixed(8),
      forfeitedRows: forfeit.length,
      executorStatus: "suspended",
    };
  }
}
