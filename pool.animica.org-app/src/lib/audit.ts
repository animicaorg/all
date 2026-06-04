// Audit log helper. Every state-changing operation calls auditLog().
// The dashboard surfaces the most recent N entries per entity.

import { prisma } from "@/server/db";

export async function auditLog(opts: {
  action: string;
  entityType: string;
  entityId?: string | null;
  actor?: string | null;
  metadata?: Record<string, unknown> | null;
}): Promise<void> {
  try {
    await prisma.auditLog.create({
      data: {
        action: opts.action,
        entityType: opts.entityType,
        entityId: opts.entityId ?? null,
        actor: opts.actor ?? "system",
        metadata: (opts.metadata ?? undefined) as object | undefined,
      },
    });
  } catch (err) {
    // Audit must NEVER fail the calling request — log and move on.
    // eslint-disable-next-line no-console
    console.error("[audit] failed:", err);
  }
}
