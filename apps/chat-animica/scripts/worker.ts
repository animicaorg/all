import { Worker } from "bullmq";
import { redis } from "@/src/server/db/redis";
import { prisma } from "@/src/server/db/prisma";
import { compile } from "@/src/server/compiler/compilerAdapter";
import { simulateAdmission } from "@/src/server/simulate/simulateAdapter";
import { defensiveSendRawTransaction, discover, rpcCall } from "@/src/server/rpc/animicaRpc";

new Worker("compile", async (job) => {
  const result = await compile(job.data.source);
  await prisma.contract.update({ where: { id: job.data.contractId }, data: { bytecode: result.bytecode, compileOutput: result as any } });
}, { connection: redis });

new Worker("simulate", async (job) => {
  const result = await simulateAdmission(job.data.bytecode);
  await prisma.deployJob.update({ where: { id: job.data.deployId }, data: { simulationOutput: result as any } });
}, { connection: redis });

new Worker("deploy", async (job) => {
  const result = await defensiveSendRawTransaction(job.data.rawTx);
  await prisma.deployJob.update({ where: { id: job.data.deployId }, data: { txHash: result.ok ? (result.txHash as string) : null, rpcAttempts: result.attempts as any, status: result.ok ? "SUBMITTED" : "FAILED", error: result.ok ? null : (result.error as any) } });
}, { connection: redis });

new Worker("tx-status", async (job) => {
  const disc = await discover();
  const names = disc.methods.map((m) => m.name);
  const method = ["tx.getStatus", "tx.getTransactionReceipt", "tx.getTransactionByHash"].find((m) => names.includes(m));
  if (!method) return;
  const status = await rpcCall(method, [job.data.txHash]);
  await prisma.deployJob.update({ where: { id: job.data.deployId }, data: { status: status?.status ?? "CONFIRMED", receipt: status as any } });
}, { connection: redis });

console.log("Workers started: compile, simulate, deploy, tx-status");
