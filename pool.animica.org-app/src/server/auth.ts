// Passwordless magic-link auth + cookie sessions.
//
// Flow: request-link → email a one-time link → verify consumes the token,
// upserts the User, mints a Session, and sets an httpOnly cookie. Tokens are
// stored only as SHA-256 hashes. Adapted from the animica-chat auth flow for
// the Next.js App Router.

import { cookies, headers } from "next/headers";
import { prisma } from "./db";
import { env } from "./env";
import { sendMagicLinkEmail } from "./mailer";
import { newOpaqueToken, hashToken, hashIp } from "@/lib/tokens";

const COOKIE_NAME = "animica_rental_session";
const MAGIC_LINK_TTL_MS = 15 * 60 * 1000;
const SESSION_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export interface SessionUser {
  id: string;
  email: string;
  role: "USER" | "ADMIN" | "BANNED";
  isAdmin: boolean;
}

function adminEmails(): Set<string> {
  return new Set(
    env()
      .ADMIN_EMAILS.split(",")
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean),
  );
}

function clientIpHash(): string | null {
  const h = headers();
  const fwd = h.get("x-forwarded-for") || h.get("x-real-ip") || "";
  const ip = fwd.split(",")[0]?.trim();
  return hashIp(ip);
}

/** Issue a magic link for `email` and email it. Always succeeds silently to
 *  avoid account enumeration; returns the link (dev/console use only). */
export async function issueMagicLink(email: string, redirectTo?: string): Promise<string> {
  const normalized = email.trim().toLowerCase();
  const token = newOpaqueToken();
  const safeRedirect = redirectTo && /^\/[^/]/.test(redirectTo) ? redirectTo : null;
  await prisma.magicLink.create({
    data: {
      tokenHash: hashToken(token),
      email: normalized,
      redirectTo: safeRedirect,
      expiresAt: new Date(Date.now() + MAGIC_LINK_TTL_MS),
    },
  });
  const link = `${env().PUBLIC_BASE_URL}/api/auth/verify?token=${encodeURIComponent(token)}`;
  await sendMagicLinkEmail({ to: normalized, link });
  return link;
}

/** Consume a magic-link token: upsert user, start a session, set the cookie.
 *  Returns the redirectTo (same-origin path) or null. Throws on invalid. */
export async function consumeMagicLink(token: string): Promise<{ redirectTo: string | null }> {
  const tokenHash = hashToken(token);
  const link = await prisma.magicLink.findUnique({ where: { tokenHash } });
  if (!link || link.consumedAt || link.expiresAt < new Date()) {
    throw new Error("This sign-in link is invalid or has expired.");
  }
  await prisma.magicLink.update({
    where: { tokenHash },
    data: { consumedAt: new Date() },
  });
  const user = await prisma.user.upsert({
    where: { email: link.email },
    update: {},
    create: { email: link.email },
  });
  await startSession(user.id);
  return { redirectTo: link.redirectTo };
}

async function startSession(userId: string): Promise<void> {
  const token = newOpaqueToken();
  const expiresAt = new Date(Date.now() + SESSION_TTL_MS);
  await prisma.session.create({
    data: {
      userId,
      tokenHash: hashToken(token),
      expiresAt,
      ipHash: clientIpHash(),
      userAgent: headers().get("user-agent")?.slice(0, 256) ?? null,
    },
  });
  cookies().set(COOKIE_NAME, token, {
    httpOnly: true,
    secure: env().NODE_ENV === "production",
    sameSite: "lax",
    path: "/app",
    domain: env().COOKIE_DOMAIN || undefined,
    expires: expiresAt,
  });
}

export async function getSessionUser(): Promise<SessionUser | null> {
  const token = cookies().get(COOKIE_NAME)?.value;
  if (!token) return null;
  const session = await prisma.session.findUnique({
    where: { tokenHash: hashToken(token) },
    include: { user: true },
  });
  if (!session || session.expiresAt < new Date()) return null;
  const email = session.user.email.toLowerCase();
  const isAdmin = session.user.role === "ADMIN" || adminEmails().has(email);
  return {
    id: session.user.id,
    email: session.user.email,
    role: session.user.role,
    isAdmin,
  };
}

export async function requireUser(): Promise<SessionUser> {
  const user = await getSessionUser();
  if (!user) throw new Error("Not authenticated");
  if (user.role === "BANNED") throw new Error("Account suspended");
  return user;
}

export async function requireAdmin(): Promise<SessionUser> {
  const user = await requireUser();
  if (!user.isAdmin) throw new Error("Admin only");
  return user;
}

export async function endSession(): Promise<void> {
  const token = cookies().get(COOKIE_NAME)?.value;
  if (token) {
    await prisma.session.deleteMany({ where: { tokenHash: hashToken(token) } });
  }
  cookies().delete(COOKIE_NAME);
}
