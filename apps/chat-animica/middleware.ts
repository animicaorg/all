import { NextRequest, NextResponse } from "next/server";
import { verifyToken } from "@/src/server/auth/jwt";
import { SESSION_COOKIE } from "@/src/server/auth/session";

export function middleware(req: NextRequest) {
  if (!req.nextUrl.pathname.startsWith("/app") && !req.nextUrl.pathname.startsWith("/account")) {
    return NextResponse.next();
  }

  const token = req.cookies.get(SESSION_COOKIE)?.value;
  if (!token || !verifyToken(token)) {
    return NextResponse.redirect(new URL("/", req.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/app/:path*", "/account"]
};
