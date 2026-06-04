// POST /app/api/auth/request-link  { email, redirectTo? }
// Always returns 200 (no account enumeration). Emails a magic link.

import { NextResponse } from "next/server";
import { z } from "zod";
import { issueMagicLink } from "@/server/auth";
import { takeToken } from "@/lib/rateLimit";

export const dynamic = "force-dynamic";

const Body = z.object({
  email: z.string().email(),
  redirectTo: z.string().optional(),
});

export async function POST(req: Request) {
  const ip =
    req.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    req.headers.get("x-real-ip") ||
    "unknown";
  const limited = takeToken(`magiclink:${ip}`, { limit: 5, windowMs: 60_000 });
  if (!limited.ok) {
    return NextResponse.json({ ok: true }); // silent — don't reveal limiting
  }
  let body: z.infer<typeof Body>;
  try {
    body = Body.parse(await req.json());
  } catch {
    return NextResponse.json({ error: "invalid email" }, { status: 400 });
  }
  try {
    await issueMagicLink(body.email, body.redirectTo);
  } catch (err) {
    // Never leak details; log server-side.
    // eslint-disable-next-line no-console
    console.error("[request-link] failed", err);
  }
  return NextResponse.json({ ok: true });
}
