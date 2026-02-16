import { NextRequest, NextResponse } from "next/server";
import { requireUser } from "@/src/server/auth/require";
import { prisma } from "@/src/server/db/prisma";

export async function POST(req: NextRequest) {
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;
  const body = await req.json();
  const name = typeof body?.name === "string" ? body.name : "Untitled Project";
  const project = await prisma.project.create({ data: { userId: userResult.user.id, name } });
  return NextResponse.json(project);
}
