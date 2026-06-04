import { PrismaService } from "../prisma/prisma.service";

export async function auditLog(
  prisma: PrismaService,
  entry: { action: string; entityType: string; entityId?: string; actor?: string; metadata?: unknown },
): Promise<void> {
  try {
    await prisma.auditLog.create({
      data: {
        action: entry.action,
        entityType: entry.entityType,
        entityId: entry.entityId,
        actor: entry.actor ?? "system",
        metadata: (entry.metadata ?? {}) as object,
      },
    });
  } catch {
    /* audit must never break the request */
  }
}
