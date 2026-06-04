// GET /app/api/admin/rentals?status=NEEDS_ADMIN_REVIEW — admin rental list.
import { NextResponse } from "next/server";
import type { RentalStatus } from "@prisma/client";
import { requireAdmin } from "@/server/auth";
import { prisma } from "@/server/db";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  try {
    await requireAdmin();
  } catch {
    return NextResponse.json({ error: "admin only" }, { status: 403 });
  }
  const status = new URL(req.url).searchParams.get("status") as RentalStatus | null;
  const rentals = await prisma.rental.findMany({
    where: status ? { status } : undefined,
    orderBy: { createdAt: "desc" },
    take: 200,
    include: { rig: { select: { workerName: true } } },
  });
  return NextResponse.json({ items: rentals });
}
