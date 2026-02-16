import { NextRequest, NextResponse } from "next/server";
import { requireActiveSubscription, requireUser } from "@/src/server/auth/require";
import { deploySchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { deployQueue, simulateQueue, txStatusQueue } from "@/src/server/jobs/queue";
import { canUseDevSigner, signWithDevSigner } from "@/src/server/wallet/devSigner";

export async function POST(req: NextRequest) {
  const userResult = await requireUser();
  if ("error" in userResult) return userResult.error;
  const subResult = await requireActiveSubscription(userResult.user.id);
  if ("error" in subResult) return subResult.error;

  const body = await req.json();
  const parsed = deploySchema.safeParse(body);
  if (!parsed.success) return NextResponse.json({ error: parsed.error.flatten() }, { status: 400 });

  const contract = await prisma.contract.findUnique({ where: { id: parsed.data.contractId } });
  if (!contract) return NextResponse.json({ error: "Contract not found" }, { status: 404 });

  const deploy = await prisma.deployJob.create({
    data: {
      userId: userResult.user.id,
      contractId: contract.id,
      status: "PENDING",
      txDraft: parsed.data.txDraft as any
    }
  });

  const rawTx = parsed.data.signedRawTx
    ?? parsed.data.rawTx
    ?? (canUseDevSigner() && parsed.data.txDraft ? signWithDevSigner(parsed.data.txDraft) : undefined);

  if (!rawTx) {
    return NextResponse.json({ error: "No signedRawTx provided. Use Animica wallet provider or enable DEV_SIGNER_KEY." }, { status: 400 });
  }

  await simulateQueue.add("simulate", { deployId: deploy.id, bytecode: contract.bytecode ?? "0x" });
  await deployQueue.add("deploy", { deployId: deploy.id, rawTx });
  await txStatusQueue.add("track", { deployId: deploy.id, txHash: "pending" }, { delay: 3_000 });

  return NextResponse.json({ ok: true, deployId: deploy.id });
}
