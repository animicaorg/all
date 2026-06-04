import { Injectable, NotFoundException } from "@nestjs/common";
import { createHash, randomBytes } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { auditLog } from "../common/audit";

export function hashApiKey(raw: string): string {
  return createHash("sha256").update(raw).digest("hex");
}

@Injectable()
export class ApiKeysService {
  constructor(private readonly prisma: PrismaService) {}

  async list(userId: string) {
    return this.prisma.apiKey.findMany({
      where: { userId, revokedAt: null },
      select: { id: true, prefix: true, label: true, isActive: true, lastUsedAt: true, createdAt: true },
      orderBy: { createdAt: "desc" },
    });
  }

  /** Mint a key. The raw secret is returned ONCE and never stored. */
  async create(userId: string, label?: string) {
    const raw = `anm_live_${randomBytes(24).toString("hex")}`;
    const prefix = raw.slice(0, 16);
    const key = await this.prisma.apiKey.create({
      data: { userId, keyHash: hashApiKey(raw), prefix, label: label ?? null },
      select: { id: true, prefix: true, label: true, createdAt: true },
    });
    await auditLog(this.prisma, { action: "apikey.create", entityType: "ApiKey", entityId: key.id, actor: userId });
    return { ...key, key: raw };
  }

  async revoke(userId: string, id: string) {
    const key = await this.prisma.apiKey.findUnique({ where: { id } });
    if (!key || key.userId !== userId) throw new NotFoundException();
    await this.prisma.apiKey.update({ where: { id }, data: { isActive: false, revokedAt: new Date() } });
    await auditLog(this.prisma, { action: "apikey.revoke", entityType: "ApiKey", entityId: id, actor: userId });
    return { ok: true };
  }

  /** Resolve a raw key to its owner (used by the /v1 inference auth in P2). */
  async resolve(raw: string) {
    const key = await this.prisma.apiKey.findUnique({
      where: { keyHash: hashApiKey(raw) },
      include: { user: { select: { id: true, email: true, role: true, isActive: true } } },
    });
    if (!key || !key.isActive || key.revokedAt) return null;
    void this.prisma.apiKey
      .update({ where: { id: key.id }, data: { lastUsedAt: new Date() } })
      .catch(() => {});
    return key;
  }
}
