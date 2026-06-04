// POST /app/api/owner/claim  { address }
// Returns a claim-worker token; the owner mines with it for ~2 min to prove
// control of the address before listing a rig.

import { NextResponse } from "next/server";
import { z } from "zod";
import { requireUser } from "@/server/auth";
import { poolClient } from "@/server/poolClient";
import { isAnim1 } from "@/lib/rig";

export const dynamic = "force-dynamic";

const Body = z.object({ address: z.string() });

export async function POST(req: Request) {
  try {
    await requireUser();
  } catch {
    return NextResponse.json({ error: "auth required" }, { status: 401 });
  }
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "address required" }, { status: 400 });
  }
  if (!isAnim1(body.address)) {
    return NextResponse.json({ error: "address must be a bech32 anim1… address" }, { status: 400 });
  }
  try {
    const challenge = await poolClient.ownershipChallenge(body.address);
    return NextResponse.json(challenge);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 502 });
  }
}
