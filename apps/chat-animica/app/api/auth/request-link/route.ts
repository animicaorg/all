import { NextRequest, NextResponse } from "next/server";
import { requestLinkSchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { signToken } from "@/src/server/auth/jwt";
import { sendMagicLinkEmail } from "@/src/server/auth/email";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const parsed = requestLinkSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

  const email = parsed.data.email.toLowerCase();
  const user = await prisma.user.upsert({ where: { email }, update: {}, create: { email } });
  const token = signToken({ userId: user.id, email }, "20m");
  const verifyUrl = `${req.nextUrl.origin}/api/auth/verify?token=${encodeURIComponent(token)}`;
  await sendMagicLinkEmail(email, verifyUrl);

  return NextResponse.json({ ok: true });
}
