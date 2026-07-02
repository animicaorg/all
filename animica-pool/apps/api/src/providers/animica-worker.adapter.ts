import { Prisma } from "@prisma/client";
import type {
  ProviderAdapter, InferenceRequest, ProviderHealthResult,
  ProviderCostEstimate, ProviderInferenceResult, ProviderEmbeddingResult,
} from "@animica/shared";
import { estimateTokens, requestInputText } from "@animica/provider-router";
import type { PrismaService } from "../prisma/prisma.service";

const WORKER_RATE = { in: 0.00006, out: 0.00018 }; // what we pay the worker per 1K tok
const POLL_MS = 500;
// Generous default — CPU inference on larger models is slow. Override with
// WORKER_INFERENCE_TIMEOUT_MS.
const DEFAULT_TIMEOUT_MS = Number(process.env.WORKER_INFERENCE_TIMEOUT_MS || 120000);

function workerCost(inTok: number, outTok: number): number {
  return (inTok / 1000) * WORKER_RATE.in + (outTok / 1000) * WORKER_RATE.out;
}
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Real internal provider: dispatches an inference WorkerJob to the Animica
 *  worker pool and awaits the worker's result. Enabled only when a verified,
 *  online inference-capable worker exists; times out → router falls back. */
export class AnimicaWorkerAdapter implements ProviderAdapter {
  name = "animica_worker";
  constructor(private readonly prisma: PrismaService, private readonly timeoutMs = DEFAULT_TIMEOUT_MS) {}

  private onlineWorkers() {
    return this.prisma.worker.count({
      where: { status: "online", isVerified: true, earningMode: { in: ["inference", "all"] } },
    });
  }

  async isEnabled() {
    return (await this.onlineWorkers()) > 0;
  }

  async healthCheck(): Promise<ProviderHealthResult> {
    const n = await this.onlineWorkers();
    return { provider: this.name, healthy: n > 0, latencyMs: 0, error: n > 0 ? undefined : "no online workers" };
  }

  async estimateCost(req: InferenceRequest): Promise<ProviderCostEstimate> {
    const inTok = estimateTokens(requestInputText(req));
    const outTok = Math.min(req.maxTokens ?? 256, 1024);
    return { provider: this.name, estimatedCostUsd: workerCost(inTok, outTok), estimatedInputTokens: inTok, estimatedOutputTokens: outTok };
  }

  async runChatCompletion(req: InferenceRequest): Promise<ProviderInferenceResult> {
    const start = Date.now();
    const job = await this.prisma.workerJob.create({
      data: {
        kind: "inference", status: "queued", model: req.model,
        payload: { messages: req.messages, prompt: req.prompt, temperature: req.temperature, maxTokens: req.maxTokens } as object,
        rewardUsd: new Prisma.Decimal(workerCost(estimateTokens(requestInputText(req)), Math.min(req.maxTokens ?? 256, 512))),
      },
    });

    // Await a worker to claim + complete the job.
    const deadline = Date.now() + this.timeoutMs;
    while (Date.now() < deadline) {
      await sleep(POLL_MS);
      const cur = await this.prisma.workerJob.findUnique({ where: { id: job.id } });
      if (!cur) break;
      if (cur.status === "completed") {
        const inTok = cur.inputTokens || estimateTokens(requestInputText(req));
        const outTok = cur.outputTokens || estimateTokens(cur.resultText ?? "");
        return {
          provider: this.name, model: req.model, requestId: req.requestId, status: "success",
          inputTokens: inTok, outputTokens: outTok, providerCostUsd: workerCost(inTok, outTok),
          latencyMs: Date.now() - start, output: cur.resultText ?? "",
        };
      }
      if (cur.status === "failed") throw new Error(cur.errorMessage || "worker job failed");
    }
    // Timed out — release the job so it isn't stuck claimed, then fall back.
    await this.prisma.workerJob.updateMany({ where: { id: job.id, status: { in: ["queued", "claimed"] } }, data: { status: "failed", errorMessage: "timed out waiting for worker" } });
    throw new Error("no worker completed the job in time");
  }

  async runEmbedding(req: InferenceRequest): Promise<ProviderEmbeddingResult> {
    const start = Date.now();
    const inputs = Array.isArray(req.input) ? req.input : [req.input ?? ""];
    const inTokEst = inputs.reduce((s, t) => s + estimateTokens(String(t)), 0);
    const job = await this.prisma.workerJob.create({
      data: {
        kind: "embedding", status: "queued", model: req.model,
        payload: { input: req.input } as object,
        rewardUsd: new Prisma.Decimal(workerCost(inTokEst, 0)),
      },
    });

    // Await a worker to claim + complete the job; the worker JSON-encodes the
    // embedding vector(s) into resultText.
    const deadline = Date.now() + this.timeoutMs;
    while (Date.now() < deadline) {
      await sleep(POLL_MS);
      const cur = await this.prisma.workerJob.findUnique({ where: { id: job.id } });
      if (!cur) break;
      if (cur.status === "completed") {
        let embeddings: number[][];
        try {
          embeddings = JSON.parse(cur.resultText ?? "[]");
        } catch {
          throw new Error("worker returned unparseable embeddings");
        }
        if (!Array.isArray(embeddings) || embeddings.length === 0 || !Array.isArray(embeddings[0])) {
          throw new Error("worker returned malformed embeddings");
        }
        const inTok = cur.inputTokens || inTokEst;
        return {
          provider: this.name, model: req.model, requestId: req.requestId, status: "success",
          inputTokens: inTok, providerCostUsd: workerCost(inTok, 0),
          latencyMs: Date.now() - start, embeddings,
        };
      }
      if (cur.status === "failed") throw new Error(cur.errorMessage || "worker embedding job failed");
    }
    await this.prisma.workerJob.updateMany({ where: { id: job.id, status: { in: ["queued", "claimed"] } }, data: { status: "failed", errorMessage: "timed out waiting for worker" } });
    throw new Error("no worker completed the embedding job in time");
  }
}
