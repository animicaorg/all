import { NextRequest, NextResponse } from "next/server";
import { requireActiveSubscription, requireUser } from "@/src/server/auth/require";
import { deploySchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { deployQueue, simulateQueue, txStatusQueue } from "@/src/server/jobs/queue";
import { resolveRawTransaction } from "@/src/server/wallet/signer";

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

  const signerResult = await resolveRawTransaction({ ...parsed.data, userId: userResult.user.id });
  if (!signerResult.rawTx) {
    return NextResponse.json({ error: signerResult.error ?? "Unable to sign transaction" }, { status: 400 });
  }

  await simulateQueue.add("simulate", { deployId: deploy.id, bytecode: contract.bytecode ?? "0x" });
  await deployQueue.add("deploy", { deployId: deploy.id, rawTx: signerResult.rawTx });
  await txStatusQueue.add("track", { deployId: deploy.id, txHash: "pending" }, { delay: 3_000 });

  return NextResponse.json({ ok: true, deployId: deploy.id, signer: parsed.data.signerType ?? "auto" });
}
