// POST /app/api/rentals/quote  { rigId, hours }
import { NextResponse } from "next/server";
import { z } from "zod";
import { prisma } from "@/server/db";
import { quoteRental } from "@/server/rentals";

export const dynamic = "force-dynamic";

const Body = z.object({
  rigId: z.string(),
  hours: z.number().int().positive().max(24 * 30),
});

export async function POST(req: Request) {
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "rigId and positive integer hours required" }, { status: 400 });
  }
  const rig = await prisma.rig.findUnique({ where: { rigId: body.rigId } });
  if (!rig || rig.status !== "ACTIVE") {
    return NextResponse.json({ error: "rig not available" }, { status: 404 });
  }
  const quote = quoteRental({ pricePerHourUsd: rig.pricePerHourUsd, hours: body.hours });
  return NextResponse.json({ rigId: rig.rigId, supportedCoins: rig.supportedCoins, ...quote });
}
