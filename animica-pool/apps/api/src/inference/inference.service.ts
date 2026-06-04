import { Injectable, UnauthorizedException, BadRequestException, HttpException, HttpStatus } from "@nestjs/common";
import { Prisma } from "@prisma/client";
import { randomBytes } from "node:crypto";
import { PrismaService } from "../prisma/prisma.service";
import { ApiKeysService } from "../api-keys/api-keys.service";
import { CreditsService } from "../credits/credits.service";
import { MODELS, MODEL_PRICING_USD_PER_1K } from "@animica/shared";
import type { InferenceRequest, ProviderAdapter } from "@animica/shared";
import { routeChatCompletion, routeEmbedding, estimateTokens, requestInputText } from "@animica/provider-router";
import { ProvidersService } from "../providers/providers.service";
import { buildAllAdapters } from "../providers/adapter-registry";
import { RevenueService } from "../revenue/revenue.service";

function genId(prefix: string): string {
  return `${prefix}_${randomBytes(12).toString("hex")}`;
}

function customerCost(model: string, inTok: number, outTok: number): number {
  const p = MODEL_PRICING_USD_PER_1K[model] ?? { in: 0.0005, out: 0.0015 };
  return (inTok / 1000) * p.in + (outTok / 1000) * p.out;
}

@Injectable()
export class InferenceService {
  private adapters: ProviderAdapter[];

  constructor(
    private readonly prisma: PrismaService,
    private readonly apiKeys: ApiKeysService,
    private readonly credits: CreditsService,
    private readonly providers: ProvidersService,
    private readonly revenue: RevenueService,
  ) {
    this.adapters = buildAllAdapters(prisma);
  }

  /** Adapters minus any an admin has explicitly disabled (ProviderConfig). */
  private async usableAdapters() {
    const disabled = await this.providers.getDisabledNames();
    return this.adapters.filter((a) => !disabled.has(a.name));
  }

  async authKey(authHeader?: string) {
    const raw = authHeader?.startsWith("Bearer ") ? authHeader.slice(7).trim() : "";
    if (!raw) throw new UnauthorizedException("Missing API key");
    const key = await this.apiKeys.resolve(raw);
    if (!key || !key.user.isActive) throw new UnauthorizedException("Invalid API key");
    return key;
  }

  listModels() {
    return {
      object: "list",
      data: MODELS.map((id) => ({ id, object: "model", created: 0, owned_by: "animica" })),
    };
  }

  private assertModel(model: string) {
    if (!MODELS.includes(model as (typeof MODELS)[number])) {
      throw new BadRequestException(`Unknown model '${model}'`);
    }
  }

  private async run(
    kind: "chat" | "completion",
    keyUserId: string,
    apiKeyId: string | null,
    body: { model: string; messages?: InferenceRequest["messages"]; prompt?: string; temperature?: number; max_tokens?: number },
  ) {
    this.assertModel(body.model);
    const req: InferenceRequest = {
      requestId: genId("req"),
      model: body.model,
      kind,
      messages: body.messages,
      prompt: body.prompt,
      temperature: body.temperature,
      maxTokens: body.max_tokens,
    };

    // Pre-flight credit gate (estimate before running).
    const estIn = estimateTokens(requestInputText(req));
    const estOut = Math.min(body.max_tokens ?? 256, 1024);
    const estCost = customerCost(body.model, estIn, estOut);
    const balance = await this.credits.balance(keyUserId);
    if (balance < estCost) {
      throw new HttpException(
        { error: { message: "Insufficient credits", type: "insufficient_quota", code: "insufficient_credits" } },
        HttpStatus.PAYMENT_REQUIRED,
      );
    }

    let result;
    try {
      result = (await routeChatCompletion(req, await this.usableAdapters())).result;
      await this.providers.recordHealth(result.provider, true, result.latencyMs);
    } catch (e) {
      await this.log(keyUserId, apiKeyId, req, "router", { in: estIn, out: 0 }, 0, 0, "failed", String(e));
      throw new HttpException(
        { error: { message: `Inference failed: ${e}`, type: "provider_error" } },
        HttpStatus.BAD_GATEWAY,
      );
    }

    const cost = customerCost(body.model, result.inputTokens, result.outputTokens);
    await this.log(keyUserId, apiKeyId, req, result.provider, { in: result.inputTokens, out: result.outputTokens }, cost, result.providerCostUsd, "success", undefined, result.latencyMs);
    await this.credits.deduct(keyUserId, cost, "inference_usage", req.requestId);
    await this.revenue.record({ sourceType: "inference", sourceId: req.requestId, grossUsd: cost, costUsd: result.providerCostUsd });
    return { req, result, cost };
  }

  async chatCompletion(authHeader: string | undefined, body: any) {
    const key = await this.authKey(authHeader);
    return this.chatResponse(key.user.id, key.id, body);
  }

  /** Session-authed variant for the web playground — no API key, charges the
   *  logged-in user's credits directly (apiKeyId logged as null). */
  async chatCompletionForUser(userId: string, body: any) {
    return this.chatResponse(userId, null, body);
  }

  private async chatResponse(userId: string, apiKeyId: string | null, body: any) {
    const { req, result } = await this.run("chat", userId, apiKeyId, body);
    return {
      id: req.requestId.replace("req_", "chatcmpl_"),
      object: "chat.completion",
      created: Math.floor(Date.now() / 1000),
      model: body.model,
      choices: [{ index: 0, message: { role: "assistant", content: result.output }, finish_reason: "stop" }],
      usage: { prompt_tokens: result.inputTokens, completion_tokens: result.outputTokens, total_tokens: result.inputTokens + result.outputTokens },
    };
  }

  async completion(authHeader: string | undefined, body: any) {
    const key = await this.authKey(authHeader);
    const { req, result } = await this.run("completion", key.user.id, key.id, body);
    return {
      id: req.requestId.replace("req_", "cmpl_"),
      object: "text_completion",
      created: Math.floor(Date.now() / 1000),
      model: body.model,
      choices: [{ index: 0, text: result.output, finish_reason: "stop" }],
      usage: { prompt_tokens: result.inputTokens, completion_tokens: result.outputTokens, total_tokens: result.inputTokens + result.outputTokens },
    };
  }

  async embeddings(authHeader: string | undefined, body: any) {
    const key = await this.authKey(authHeader);
    this.assertModel(body.model);
    const req: InferenceRequest = { requestId: genId("req"), model: body.model, kind: "embedding", input: body.input };
    const estIn = estimateTokens(requestInputText(req));
    const estCost = customerCost(body.model, estIn, 0);
    if ((await this.credits.balance(key.user.id)) < estCost) {
      throw new HttpException({ error: { message: "Insufficient credits", code: "insufficient_credits" } }, HttpStatus.PAYMENT_REQUIRED);
    }
    const result = await routeEmbedding(req, await this.usableAdapters());
    const cost = customerCost(body.model, result.inputTokens, 0);
    await this.log(key.user.id, key.id, req, result.provider, { in: result.inputTokens, out: 0 }, cost, result.providerCostUsd, "success", undefined, result.latencyMs);
    await this.credits.deduct(key.user.id, cost, "inference_usage", req.requestId);
    await this.revenue.record({ sourceType: "inference", sourceId: req.requestId, grossUsd: cost, costUsd: result.providerCostUsd });
    return {
      object: "list",
      data: result.embeddings.map((embedding, index) => ({ object: "embedding", index, embedding })),
      model: body.model,
      usage: { prompt_tokens: result.inputTokens, total_tokens: result.inputTokens },
    };
  }

  async usage(authHeader: string | undefined) {
    const key = await this.authKey(authHeader);
    const since = new Date(Date.now() - 30 * 24 * 60 * 60 * 1000);
    const rows = await this.prisma.inferenceRequest.findMany({
      where: { userId: key.user.id, createdAt: { gte: since } },
      orderBy: { createdAt: "desc" },
      take: 100,
    });
    const totalUsd = rows.reduce((s, r) => s + Number(r.customerCostUsd), 0);
    return { object: "list", totalSpentUsd: totalUsd, count: rows.length, data: rows };
  }

  private async log(
    userId: string, apiKeyId: string | null, req: InferenceRequest, provider: string,
    tok: { in: number; out: number }, customerCostUsd: number, providerCostUsd: number,
    status: "success" | "failed", errorMessage?: string, latencyMs?: number,
  ) {
    await this.prisma.inferenceRequest.create({
      data: {
        userId, apiKeyId, model: req.model, provider, kind: req.kind,
        inputTokens: tok.in, outputTokens: tok.out,
        customerCostUsd: new Prisma.Decimal(customerCostUsd),
        providerCostUsd: new Prisma.Decimal(providerCostUsd),
        grossMarginUsd: new Prisma.Decimal(customerCostUsd - providerCostUsd),
        latencyMs, status, errorMessage,
      },
    }).catch(() => {});
  }
}
