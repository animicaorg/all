// PATCH  /app/api/owner/listings/[id]  → pause/resume or edit price/payout
// DELETE /app/api/owner/listings/[id]  → unlist
import { NextResponse } from "next/server";
import { z } from "zod";
import { requireUser } from "@/server/auth";
import { prisma } from "@/server/db";
import { auditLog } from "@/lib/audit";

export const dynamic = "force-dynamic";

const PatchBody = z.object({
  status: z.enum(["ACTIVE", "PAUSED", "UNLISTED"]).optional(),
  pricePerHourUsd: z.string().optional(),
  payoutCurrency: z.string().optional(),
  payoutAddress: z.string().optional(),
});

async function ownRig(userId: string, id: string) {
  const rig = await prisma.rig.findUnique({ where: { id } });
  if (!rig || rig.ownerUserId !== userId) return null;
  return rig;
}

export async function PATCH(req: Request, { params }: { params: { id: string } }) {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  const rig = await ownRig(user.id, params.id);
  if (!rig) return NextResponse.json({ error: "not found" }, { status: 404 });
  let body: z.infer<typeof PatchBody>;
  try {
    body = PatchBody.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid body" }, { status: 400 });
  }
  if (body.pricePerHourUsd !== undefined) {
    const p = Number(body.pricePerHourUsd);
    if (!Number.isFinite(p) || p <= 0) {
      return NextResponse.json({ error: "pricePerHourUsd must be > 0" }, { status: 400 });
    }
  }
  const updated = await prisma.rig.update({
    where: { id: rig.id },
    data: {
      ...(body.status ? { status: body.status } : {}),
      ...(body.pricePerHourUsd ? { pricePerHourUsd: body.pricePerHourUsd } : {}),
      ...(body.payoutCurrency ? { payoutCurrency: body.payoutCurrency.toLowerCase() } : {}),
      ...(body.payoutAddress ? { payoutAddress: body.payoutAddress } : {}),
    },
  });
  await auditLog({
    action: "rig.updated",
    entityType: "Rig",
    entityId: rig.id,
    actor: user.email,
    metadata: { ...body },
  });
  return NextResponse.json({ rig: updated });
}

export async function DELETE(_req: Request, { params }: { params: { id: string } }) {
  let user;
  try {
    user = await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  const rig = await ownRig(user.id, params.id);
  if (!rig) return NextResponse.json({ error: "not found" }, { status: 404 });
  const updated = await prisma.rig.update({
    where: { id: rig.id },
    data: { status: "UNLISTED" },
  });
  await auditLog({ action: "rig.unlisted", entityType: "Rig", entityId: rig.id, actor: user.email });
  return NextResponse.json({ rig: updated });
}
