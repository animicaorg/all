import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { createSubscriptionLink } from "@/src/server/paypal/client";

export async function POST(req: NextRequest) {
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;

  const url = await createSubscriptionLink(`${req.nextUrl.origin}/account`, `${req.nextUrl.origin}/pricing`);
  return NextResponse.redirect(url);
}
