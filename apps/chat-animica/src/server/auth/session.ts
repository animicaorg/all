import { cookies } from "next/headers";
import { prisma } from "@/src/server/db/prisma";
import { verifyToken } from "@/src/server/auth/jwt";

export const SESSION_COOKIE = "animica_session";

export async function getSessionUser() {
  const cookieStore = cookies();
  const raw = cookieStore.get(SESSION_COOKIE)?.value;
  if (!raw) return null;
  const payload = verifyToken(raw);
  if (!payload) return null;
  return prisma.user.findUnique({ where: { id: payload.userId } });
}
