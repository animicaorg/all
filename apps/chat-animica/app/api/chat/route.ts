import { NextRequest, NextResponse } from "next/server";
import { requireActiveSubscription, requireUser } from "@/src/server/auth/require";
import { consumeDailyMessage } from "@/src/server/usage/rateLimit";
import { callModalChat } from "@/src/modal/modalClient";
import { chatSchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { compileQueue } from "@/src/server/jobs/queue";
import { getRequestLogger } from "@/src/server/logging/requestLogger";

export async function POST(req: NextRequest) {
  const log = getRequestLogger(req);
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;
  const subResult = await requireActiveSubscription(userResult.user.id);
  if ("error" in subResult) return subResult.error;

  const body = Object.fromEntries((await req.formData()).entries());
  const parsed = chatSchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

  const usage = await consumeDailyMessage(userResult.user.id);
  if (!usage.allowed) {
    log.warn({ userId: userResult.user.id, usage }, "Daily chat limit exceeded");
    return NextResponse.json({ error: "Daily limit exceeded", usage }, { status: 429 });
  }

  const project = parsed.data.projectId
    ? await prisma.project.findUnique({ where: { id: parsed.data.projectId } })
    : await prisma.project.create({ data: { userId: userResult.user.id, name: `Project ${new Date().toISOString()}` } });

  const thread = parsed.data.threadId
    ? await prisma.thread.findUnique({ where: { id: parsed.data.threadId } })
    : await prisma.thread.create({ data: { projectId: project!.id, title: "New thread" } });

  const output = await callModalChat({ prompt: parsed.data.prompt, context: { projectId: project!.id, threadId: thread!.id } });

  const contract = await prisma.contract.create({
    data: {
      projectId: project!.id,
      threadId: thread!.id,
      source: output.content,
      abiJson: output.abi as any,
      manifestJson: output.manifest as any
    }
  });

  await compileQueue.add("compile", { contractId: contract.id, source: output.content });

  return NextResponse.json({ ok: true, contractId: contract.id, usage, output });
}
