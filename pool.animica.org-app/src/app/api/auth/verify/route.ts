// GET /app/api/auth/verify?token=...  — consume a magic link, set the
// session cookie, and redirect into the app.

import { NextResponse } from "next/server";
import { consumeMagicLink } from "@/server/auth";
import { env } from "@/server/env";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const token = url.searchParams.get("token") || "";
  const base = env().PUBLIC_BASE_URL;
  if (!token) {
    return NextResponse.redirect(`${base}/login?error=missing`);
  }
  try {
    const { redirectTo } = await consumeMagicLink(token);
    const dest = redirectTo ? `${base}${redirectTo}` : `${base}/`;
    return NextResponse.redirect(dest);
  } catch {
    return NextResponse.redirect(`${base}/login?error=expired`);
  }
}
