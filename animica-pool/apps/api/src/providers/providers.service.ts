import { Injectable, NotFoundException } from "@nestjs/common";
import { Prisma } from "@prisma/client";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";
import { PROVIDER_PRIORITY } from "@animica/provider-router";
import type { ProviderAdapter } from "@animica/shared";
import { buildAllAdapters } from "./adapter-registry";

@Injectable()
export class ProvidersService {
  private adapters: ProviderAdapter[];

  constructor(private readonly prisma: PrismaService) {
    this.adapters = buildAllAdapters(prisma);
  }

  private adapter(name: string): ProviderAdapter | undefined {
    return this.adapters.find((a) => a.name === name);
  }

  /** Public health view — never exposes API keys or endpoints. */
  async list() {
    const [configs, healths] = await Promise.all([
      this.prisma.providerConfig.findMany(),
      this.prisma.providerHealth.findMany(),
    ]);
    const cfgByName = new Map(configs.map((c) => [c.name, c]));
    const healthByName = new Map(healths.map((h) => [h.provider, h]));
    const rows = await Promise.all(
      this.adapters.map(async (a) => {
        const cfg = cfgByName.get(a.name);
        const health = healthByName.get(a.name);
        return {
          name: a.name,
          capableEnv: await a.isEnabled().catch(() => false),
          enabled: cfg ? cfg.isEnabled : true,
          priority: cfg?.priority ?? PROVIDER_PRIORITY[a.name] ?? 500,
          avgLatencyMs: health?.avgLatencyMs ?? 0,
          successRate: health ? Number(health.successRate) : 100,
          currentQueue: health?.currentQueue ?? 0,
          lastError: health?.lastError ?? null,
          updatedAt: health?.updatedAt ?? null,
        };
      }),
    );
    return rows.sort((a, b) => a.priority - b.priority);
  }

  /** Live health check for one provider; persists the result. */
  async test(name: string) {
    const a = this.adapter(name);
    if (!a) throw new NotFoundException(`Unknown provider '${name}'`);
    const result = await a.healthCheck();
    await this.prisma.providerHealth.upsert({
      where: { provider: name },
      create: {
        provider: name, isEnabled: result.healthy, avgLatencyMs: result.latencyMs ?? 0,
        successRate: new Prisma.Decimal(result.healthy ? 100 : 0), lastError: result.error ?? null,
      },
      update: {
        isEnabled: result.healthy, avgLatencyMs: result.latencyMs ?? 0, lastError: result.error ?? null,
      },
    });
    return result;
  }

  async setConfig(actor: string, name: string, dto: { isEnabled?: boolean; priority?: number }) {
    if (!this.adapter(name)) throw new NotFoundException(`Unknown provider '${name}'`);
    const cfg = await this.prisma.providerConfig.upsert({
      where: { name },
      create: { name, isEnabled: dto.isEnabled ?? false, priority: dto.priority ?? (PROVIDER_PRIORITY[name] ?? 500) },
      update: { ...(dto.isEnabled !== undefined ? { isEnabled: dto.isEnabled } : {}), ...(dto.priority !== undefined ? { priority: dto.priority } : {}) },
    });
    await auditLog(this.prisma, { action: "provider.config", entityType: "ProviderConfig", entityId: name, actor, metadata: { ...dto } });
    return cfg;
  }

  /** Names explicitly disabled by an admin (ProviderConfig.isEnabled=false). */
  async getDisabledNames(): Promise<Set<string>> {
    const off = await this.prisma.providerConfig.findMany({ where: { isEnabled: false }, select: { name: true } });
    return new Set(off.map((o) => o.name));
  }

  /** Update rolling health after a real inference (EMA latency + success). */
  async recordHealth(provider: string, ok: boolean, latencyMs?: number) {
    const existing = await this.prisma.providerHealth.findUnique({ where: { provider } });
    const prevLat = existing?.avgLatencyMs ?? latencyMs ?? 0;
    const prevSucc = existing ? Number(existing.successRate) : 100;
    const newLat = latencyMs != null ? Math.round(prevLat * 0.7 + latencyMs * 0.3) : prevLat;
    const newSucc = prevSucc * 0.9 + (ok ? 100 : 0) * 0.1;
    await this.prisma.providerHealth.upsert({
      where: { provider },
      create: { provider, isEnabled: true, avgLatencyMs: newLat, successRate: new Prisma.Decimal(newSucc.toFixed(2)) },
      update: { avgLatencyMs: newLat, successRate: new Prisma.Decimal(newSucc.toFixed(2)) },
    }).catch(() => {});
  }
}
