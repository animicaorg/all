import { Injectable, UnauthorizedException, NotFoundException, ConflictException } from "@nestjs/common";
import { Prisma, WorkerStatus, WorkerEarningMode } from "@prisma/client";
import { createHash, randomBytes } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";

function hashToken(raw: string): string {
  return createHash("sha256").update(raw).digest("hex");
}

@Injectable()
export class WorkersService {
  constructor(private readonly prisma: PrismaService) {}

  // ---- User-facing (session) ----
  async listMine(userId: string) {
    return this.prisma.worker.findMany({ where: { userId }, orderBy: { createdAt: "desc" } });
  }

  /** Create a worker profile; returns the install token ONCE. */
  async create(userId: string, dto: { name: string; earningMode?: string; region?: string }) {
    const raw = `anm_worker_${randomBytes(20).toString("hex")}`;
    const worker = await this.prisma.worker.create({
      data: {
        userId,
        name: dto.name,
        region: dto.region ?? null,
        earningMode: (dto.earningMode as WorkerEarningMode) ?? "all",
        status: "pending",
        tokenHash: hashToken(raw),
        token: raw,
      },
    });
    await auditLog(this.prisma, { action: "worker.create", entityType: "Worker", entityId: worker.id, actor: userId });
    return { worker: { id: worker.id, name: worker.name, status: worker.status }, token: raw };
  }

  /** Mint a fresh token for one of the user's workers (old token stops working
   *  immediately — the rig's agent must be restarted with the new one). Covers
   *  pre-`token`-column workers whose raw token was never stored. */
  async regenerateToken(userId: string, id: string) {
    const worker = await this.prisma.worker.findUnique({ where: { id } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    const raw = `anm_worker_${randomBytes(20).toString("hex")}`;
    await this.prisma.worker.update({ where: { id }, data: { tokenHash: hashToken(raw), token: raw } });
    await auditLog(this.prisma, { action: "worker.token.regenerate", entityType: "Worker", entityId: id, actor: userId });
    return { token: raw };
  }

  /** Per-worker stats for the owner's expandable row on the Workers page. */
  async stats(userId: string, id: string) {
    const worker = await this.prisma.worker.findUnique({ where: { id } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    const [completed, failed, jobs, lastBench, recent] = await Promise.all([
      this.prisma.workerJob.count({ where: { workerId: id, status: "completed" } }),
      this.prisma.workerJob.count({ where: { workerId: id, status: "failed" } }),
      this.prisma.workerJob.findMany({ where: { workerId: id, status: "completed" }, select: { rewardUsd: true, inputTokens: true, outputTokens: true } }),
      this.prisma.workerBenchmark.findFirst({ where: { workerId: id }, orderBy: { createdAt: "desc" } }),
      this.prisma.workerJob.findMany({ where: { workerId: id }, orderBy: { createdAt: "desc" }, take: 5,
        select: { id: true, kind: true, model: true, status: true, outputTokens: true, rewardUsd: true, createdAt: true, completedAt: true } }),
    ]);
    const earningsUsd = jobs.reduce((s, j) => s + Number(j.rewardUsd ?? 0), 0);
    const tokensServed = jobs.reduce((s, j) => s + (j.inputTokens ?? 0) + (j.outputTokens ?? 0), 0);
    const onlineMs = worker.lastSeenAt ? Date.now() - worker.lastSeenAt.getTime() : null;
    const listing = await this.listingFor(id);
    const rental = {
      listed: !!listing && listing.availabilityStatus === "available",
      pricePerHourUsd: listing ? Number(listing.pricePerHourUsd) : null,
      listingId: listing?.id ?? null,
    };
    return {
      rental,
      id: worker.id,
      token: worker.token, // null for workers created before raw tokens were kept
      status: worker.status,
      isVerified: worker.isVerified,
      earningMode: worker.earningMode,
      hardware: { cpuModel: worker.cpuModel, gpuModel: worker.gpuModel, ramGb: worker.ramGb, vramGb: worker.vramGb, os: worker.os, region: worker.region },
      benchmarkScore: worker.benchmarkScore != null ? Number(worker.benchmarkScore) : null,
      lastBenchmarkAt: lastBench?.createdAt ?? null,
      lastSeenAt: worker.lastSeenAt,
      online: onlineMs != null && onlineMs < 5 * 60 * 1000,
      supportedModels: worker.supportedModels,
      jobs: { completed, failed },
      earningsUsd,
      tokensServed,
      createdAt: worker.createdAt,
      recentJobs: recent,
    };
  }

  /** Current rental-listing status for one of the user's workers. */
  private async listingFor(workerId: string) {
    return this.prisma.rentalListing.findFirst({ where: { workerId }, orderBy: { createdAt: "desc" } });
  }

  /** List (or update) one of the user's workers on the rental marketplace. */
  async listForRent(userId: string, id: string, dto: { pricePerHourUsd: number; title?: string }) {
    const worker = await this.prisma.worker.findUnique({ where: { id } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    const price = Number(dto.pricePerHourUsd);
    if (!(price > 0)) throw new ConflictException("pricePerHourUsd must be greater than 0");
    const providerType = (worker.gpuModel ? "gpu" : "cpu") as any;
    const title = (dto.title || "").trim() || `${worker.name}${worker.gpuModel ? ` · ${worker.gpuModel}` : worker.cpuModel ? ` · ${worker.cpuModel}` : ""}`;
    const data = {
      providerType,
      workerId: id,
      title,
      cpuModel: worker.cpuModel, gpuModel: worker.gpuModel, vramGb: worker.vramGb, ramGb: worker.ramGb, region: worker.region,
      pricePerHourUsd: new Prisma.Decimal(price),
      benchmarkScore: worker.benchmarkScore,
      supportedModels: worker.supportedModels,
      availabilityStatus: "available",
    };
    const existing = await this.listingFor(id);
    const listing = existing
      ? await this.prisma.rentalListing.update({ where: { id: existing.id }, data })
      : await this.prisma.rentalListing.create({ data });
    await auditLog(this.prisma, { action: "worker.rental.list", entityType: "RentalListing", entityId: listing.id, actor: userId });
    return { listed: true, listingId: listing.id, pricePerHourUsd: price };
  }

  /** Remove one of the user's workers from the rental marketplace. */
  async unlistRental(userId: string, id: string) {
    const worker = await this.prisma.worker.findUnique({ where: { id } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    const existing = await this.listingFor(id);
    if (existing) {
      await this.prisma.rentalListing.update({ where: { id: existing.id }, data: { availabilityStatus: "unlisted" } });
      await auditLog(this.prisma, { action: "worker.rental.unlist", entityType: "RentalListing", entityId: existing.id, actor: userId });
    }
    return { listed: false };
  }

  /** Delete one of the user's own workers. Cascades benchmarks; detaches jobs
   *  (WorkerJob.workerId is nullable) and any rental listing keyed to it. */
  async remove(userId: string, id: string) {
    const worker = await this.prisma.worker.findUnique({ where: { id } });
    if (!worker || worker.userId !== userId) throw new NotFoundException("Worker not found");
    await this.prisma.$transaction([
      // Retire any marketplace listing backed by this worker (workerId is a
      // loose string with no FK), so it stops being rentable.
      this.prisma.rentalListing.updateMany({ where: { workerId: id }, data: { availabilityStatus: "unlisted" } }),
      this.prisma.worker.delete({ where: { id } }),
    ]);
    await auditLog(this.prisma, { action: "worker.delete", entityType: "Worker", entityId: id, actor: userId });
    return { ok: true, id };
  }

  // ---- Machine-facing (worker token) ----
  private async fromToken(authHeader?: string) {
    const raw = authHeader?.startsWith("Bearer ") ? authHeader.slice(7).trim() : "";
    if (!raw) throw new UnauthorizedException("Missing worker token");
    const worker = await this.prisma.worker.findUnique({ where: { tokenHash: hashToken(raw) } });
    if (!worker || worker.status === "banned" || worker.status === "disabled") {
      throw new UnauthorizedException("Invalid or disabled worker token");
    }
    return worker;
  }

  async register(authHeader: string | undefined, hw: {
    machineId?: string; cpuModel?: string; gpuModel?: string; vramGb?: number;
    ramGb?: number; diskGb?: number; os?: string; region?: string; supportedModels?: string[];
  }) {
    const worker = await this.fromToken(authHeader);
    if (hw.machineId) {
      const clash = await this.prisma.worker.findUnique({ where: { machineId: hw.machineId } });
      if (clash && clash.id !== worker.id) throw new ConflictException("machineId already registered");
    }
    const updated = await this.prisma.worker.update({
      where: { id: worker.id },
      data: {
        machineId: hw.machineId ?? worker.machineId,
        cpuModel: hw.cpuModel, gpuModel: hw.gpuModel, vramGb: hw.vramGb, ramGb: hw.ramGb,
        diskGb: hw.diskGb, os: hw.os, region: hw.region ?? worker.region,
        supportedModels: hw.supportedModels ?? worker.supportedModels,
        status: "benchmarking", lastSeenAt: new Date(),
      },
    });
    await auditLog(this.prisma, { action: "worker.register", entityType: "Worker", entityId: worker.id, metadata: { machineId: hw.machineId } });
    return { ok: true, workerId: updated.id, status: updated.status };
  }

  async heartbeat(authHeader: string | undefined) {
    const worker = await this.fromToken(authHeader);
    const status: WorkerStatus = worker.status === "benchmarking" || worker.status === "pending" ? worker.status : "online";
    await this.prisma.worker.update({ where: { id: worker.id }, data: { lastSeenAt: new Date(), status } });
    // Daily uptime rollup (30s beats) — powers the Bittensor SN51 executor
    // eligibility gate (slashing protection requires proven uptime history).
    const now = new Date();
    const day = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
    await this.prisma.workerHeartbeatDay.upsert({
      where: { workerId_day: { workerId: worker.id, day } },
      create: { workerId: worker.id, day, beats: 1 },
      update: { beats: { increment: 1 } },
    }).catch(() => {});
    return { ok: true, status };
  }

  async benchmark(authHeader: string | undefined, body: { score: number; details?: unknown }) {
    const worker = await this.fromToken(authHeader);
    await this.prisma.workerBenchmark.create({
      data: { workerId: worker.id, score: new Prisma.Decimal(body.score), details: (body.details ?? {}) as object },
    });
    const updated = await this.prisma.worker.update({
      where: { id: worker.id },
      data: { benchmarkScore: new Prisma.Decimal(body.score), isVerified: true, status: "online", lastSeenAt: new Date() },
    });
    return { ok: true, status: updated.status, isVerified: updated.isVerified, benchmarkScore: Number(updated.benchmarkScore) };
  }

  async me(authHeader: string | undefined) {
    const worker = await this.fromToken(authHeader);
    return worker;
  }

  async earnings(authHeader: string | undefined) {
    const worker = await this.fromToken(authHeader);
    const jobs = await this.prisma.workerJob.findMany({ where: { workerId: worker.id, status: "completed" } });
    const totalUsd = jobs.reduce((s, j) => s + Number(j.rewardUsd ?? 0), 0);
    return { totalUsd, completedJobs: jobs.length };
  }

  async claimableJobs() {
    return this.prisma.workerJob.findMany({ where: { status: "queued" }, orderBy: { createdAt: "asc" }, take: 50 });
  }

  async claimJob(authHeader: string | undefined, jobId: string) {
    const worker = await this.fromToken(authHeader);
    // Atomic claim: only succeeds if still queued.
    const res = await this.prisma.workerJob.updateMany({
      where: { id: jobId, status: "queued" },
      data: { workerId: worker.id, status: "claimed", claimedAt: new Date() },
    });
    if (res.count === 0) throw new ConflictException("Job already claimed or missing");
    return { ok: true, jobId };
  }

  async completeJob(
    authHeader: string | undefined,
    jobId: string,
    body: { rewardUsd?: number; resultText?: string; inputTokens?: number; outputTokens?: number; error?: string },
  ) {
    const worker = await this.fromToken(authHeader);
    const job = await this.prisma.workerJob.findUnique({ where: { id: jobId } });
    if (!job || job.workerId !== worker.id) throw new NotFoundException("Job not found for this worker");
    const failed = !!body.error;
    const updated = await this.prisma.workerJob.update({
      where: { id: jobId },
      data: {
        status: failed ? "failed" : "completed",
        completedAt: new Date(),
        resultText: body.resultText,
        inputTokens: body.inputTokens ?? 0,
        outputTokens: body.outputTokens ?? 0,
        errorMessage: body.error,
        rewardUsd: body.rewardUsd != null ? new Prisma.Decimal(body.rewardUsd) : job.rewardUsd,
      },
    });
    return { ok: true, jobId, status: updated.status };
  }
}
