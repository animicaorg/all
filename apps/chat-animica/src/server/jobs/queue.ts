import { Queue } from "bullmq";
import { redis } from "@/src/server/db/redis";

export const compileQueue = new Queue("compile", { connection: redis });
export const simulateQueue = new Queue("simulate", { connection: redis });
export const deployQueue = new Queue("deploy", { connection: redis });
export const txStatusQueue = new Queue("tx-status", { connection: redis });
