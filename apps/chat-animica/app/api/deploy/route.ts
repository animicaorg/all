import { NextRequest, NextResponse } from "next/server";
import { requireActiveSubscription, requireUser } from "@/src/server/auth/require";
import { deploySchema } from "@/src/shared/schemas";
import { prisma } from "@/src/server/db/prisma";
import { resolveRawTransaction } from "@/src/server/wallet/signer";
import { buildDeployCborTx } from "@/src/server/tx/buildTx";
import { defensiveSendRawTransaction, pollReceipt } from "@/src/server/rpc/animicaRpc";
import { env } from "@/src/server/env";

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
  if (!contract.bytecode) return NextResponse.json({ error: "Contract bytecode unavailable. Compile first." }, { status: 409 });

  const txCbor = buildDeployCborTx({
    chainId: parsed.data.chainId,
    nonce: parsed.data.nonce,
    gasLimit: parsed.data.gasLimit,
    fee: parsed.data.fee,
    from: parsed.data.from,
    bytecode: contract.bytecode,
    args: parsed.data.args
  });

  const signerResult = await resolveRawTransaction({ ...parsed.data, txCbor, userId: userResult.user.id });
  if (!signerResult.rawTx) return NextResponse.json({ error: signerResult.error, signerMode: signerResult.mode }, { status: 400 });

  const deploy = await prisma.deployJob.create({
    data: {
      userId: userResult.user.id,
      contractId: contract.id,
      status: "SUBMITTING",
      txDraft: { txCbor, ...parsed.data } as any
    }
  });

  const sendResult = await defensiveSendRawTransaction(signerResult.rawTx);
  if (!sendResult.ok) {
    await prisma.deployJob.update({ where: { id: deploy.id }, data: { status: "FAILED", rpcAttempts: sendResult.attempts as any, error: sendResult.error as any } });
    return NextResponse.json({ error: sendResult.error.message, details: sendResult.error, attempts: sendResult.attempts }, { status: 502 });
  }

  let receipt: unknown;
  try {
    receipt = await pollReceipt(sendResult.txHash, 30_000, 1_500);
    await prisma.deployJob.update({ where: { id: deploy.id }, data: { status: "CONFIRMED", txHash: sendResult.txHash, receipt: receipt as any, rpcAttempts: sendResult.attempts as any } });
  } catch (error: any) {
    await prisma.deployJob.update({ where: { id: deploy.id }, data: { status: "SUBMITTED", txHash: sendResult.txHash, rpcAttempts: sendResult.attempts as any, error: { message: error.message } as any } });
  }

  return NextResponse.json({
    ok: true,
    deployId: deploy.id,
    txHash: sendResult.txHash,
    explorerUrl: env.EXPLORER_TX_URL.replace("{hash}", sendResult.txHash),
    receipt,
    rawTx: signerResult.rawTx,
    txCbor,
    signerMode: signerResult.mode
  });
}
