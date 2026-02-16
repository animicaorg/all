import { NextRequest, NextResponse } from "next/server";
import { verifyToken, signToken } from "@/src/server/auth/jwt";
import { SESSION_COOKIE } from "@/src/server/auth/session";

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get("token");
  if (!token) return NextResponse.json({ error: "Missing token" }, { status: 400 });

  const payload = verifyToken(token);
  if (!payload) return NextResponse.json({ error: "Invalid token" }, { status: 400 });

  const sessionToken = signToken({ userId: payload.userId, email: payload.email }, "7d");
  const res = NextResponse.redirect(new URL("/app", req.url));
  res.cookies.set(SESSION_COOKIE, sessionToken, {
    httpOnly: true,
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7
  });
  return res;
}
