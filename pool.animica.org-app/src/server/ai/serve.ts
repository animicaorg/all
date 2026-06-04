// Metering orchestration shared by the /v1 chat + embeddings routes.
//
//   pre-check balance > 0  →  route to a provider  →  price the call  →
//   atomically { record InferenceRequest, debit credit }  →  audit  →  reply
//
// Keeping this here means the route handlers are just parse + map-errors.

import type { AuthedKey } from "@/server/apiAuth";
import { auditLog } from "@/lib/audit";
import { gt } from "@/lib/money";
import { getModel } from "./catalog";
import { customerCost, grossMargin } from "./pricing";
import {
  debitForInference,
  getBalance,
  InsufficientCreditError,
} from "@/server/credit";
import { prisma } from "@/server/db";
import { routeChat, routeEmbed } from "@/server/providers/router";
import type { ChatMessage } from "@/server/providers/types";
import { NoProviderError, UnknownModelError } from "@/server/providers/router";
import { ProviderError } from "@/server/providers/types";
import { chatCompletionEnvelope, embeddingEnvelope, errorResponse } from "./openai";

export class PaymentRequiredError extends Error {
  constructor(public readonly balanceUsd: string) {
    super(`payment required: balance ${balanceUsd}`);
    this.name = "PaymentRequiredError";
  }
}

async function ensureFunded(userId: string): Promise<void> {
  const balance = await getBalance(userId);
  if (!gt(balance, "0")) throw new PaymentRequiredError(balance);
}

export async function serveChat(
  authed: AuthedKey,
  input: { model: string; messages: ChatMessage[]; maxTokens?: number; temperature?: number },
) {
  await ensureFunded(authed.user.id);
  const model = getModel(input.model)!; // router will reject unknown models first
  const { provider, result } = await routeChat({
    model: input.model,
    messages: input.messages,
    maxTokens: input.maxTokens,
    temperature: input.temperature,
  });

  const customerCostUsd = customerCost(model, result.inputTokens, result.outputTokens);
  const grossMarginUsd = grossMargin(customerCostUsd, result.providerCostUsd);

  let inferenceId: string;
  try {
    inferenceId = await prisma.$transaction(async (tx) => {
      const rec = await tx.inferenceRequest.create({
        data: {
          userId: authed.user.id,
          apiKeyId: authed.apiKey.id,
          model: input.model,
          provider,
          kind: "chat",
          inputTokens: result.inputTokens,
          outputTokens: result.outputTokens,
          customerCostUsd,
          providerCostUsd: result.providerCostUsd,
          grossMarginUsd,
          latencyMs: result.latencyMs,
          status: "success",
        },
      });
      await debitForInference(tx, {
        userId: authed.user.id,
        amountUsd: customerCostUsd,
        inferenceRequestId: rec.id,
      });
      return rec.id;
    }, { timeout: 15000 });
  } catch (err) {
    if (err instanceof InsufficientCreditError) {
      await recordFailed(authed, input.model, provider, "chat", err.message, {
        inputTokens: result.inputTokens,
        outputTokens: result.outputTokens,
        customerCostUsd,
        providerCostUsd: result.providerCostUsd,
        grossMarginUsd,
        latencyMs: result.latencyMs,
      });
      throw new PaymentRequiredError(err.balanceUsd);
    }
    throw err;
  }

  void auditLog({
    action: "inference.chat",
    entityType: "InferenceRequest",
    entityId: inferenceId,
    actor: authed.user.email,
    metadata: { model: input.model, provider, customerCostUsd },
  });

  return chatCompletionEnvelope({
    id: inferenceId,
    model: input.model,
    content: result.output,
    promptTokens: result.inputTokens,
    completionTokens: result.outputTokens,
  });
}

export async function serveEmbed(
  authed: AuthedKey,
  input: { model: string; input: string[]; encodingFormat?: "float" | "base64" },
) {
  await ensureFunded(authed.user.id);
  const model = getModel(input.model)!;
  const { provider, result } = await routeEmbed({ model: input.model, input: input.input });

  const customerCostUsd = customerCost(model, result.inputTokens, 0);
  const grossMarginUsd = grossMargin(customerCostUsd, result.providerCostUsd);

  try {
    await prisma.$transaction(async (tx) => {
      const rec = await tx.inferenceRequest.create({
        data: {
          userId: authed.user.id,
          apiKeyId: authed.apiKey.id,
          model: input.model,
          provider,
          kind: "embedding",
          inputTokens: result.inputTokens,
          outputTokens: 0,
          customerCostUsd,
          providerCostUsd: result.providerCostUsd,
          grossMarginUsd,
          latencyMs: result.latencyMs,
          status: "success",
        },
      });
      await debitForInference(tx, {
        userId: authed.user.id,
        amountUsd: customerCostUsd,
        inferenceRequestId: rec.id,
      });
    }, { timeout: 15000 });
  } catch (err) {
    if (err instanceof InsufficientCreditError) {
      await recordFailed(authed, input.model, provider, "embedding", err.message, {
        inputTokens: result.inputTokens,
        outputTokens: 0,
        customerCostUsd,
        providerCostUsd: result.providerCostUsd,
        grossMarginUsd,
        latencyMs: result.latencyMs,
      });
      throw new PaymentRequiredError(err.balanceUsd);
    }
    throw err;
  }

  return embeddingEnvelope({
    model: input.model,
    embeddings: result.embeddings,
    promptTokens: result.inputTokens,
    encodingFormat: input.encodingFormat,
  });
}

/** Map a thrown serve/router error to an OpenAI-style error response. */
export function mapInferenceError(err: unknown) {
  if (err instanceof PaymentRequiredError) {
    return errorResponse(
      402,
      `Insufficient credit (balance $${err.balanceUsd}). Add credits to continue.`,
      "insufficient_quota",
      "insufficient_quota",
    );
  }
  if (err instanceof UnknownModelError) {
    return errorResponse(404, err.message, "invalid_request_error", "model_not_found");
  }
  if (err instanceof NoProviderError || err instanceof ProviderError) {
    return errorResponse(
      503,
      "No provider could serve this request. Please retry shortly.",
      "server_error",
      "provider_unavailable",
    );
  }
  // eslint-disable-next-line no-console
  console.error("[serve] unexpected error:", err);
  return errorResponse(500, "Internal server error.", "server_error");
}

async function recordFailed(
  authed: AuthedKey,
  model: string,
  provider: string,
  kind: "chat" | "embedding",
  errorMessage: string,
  m: {
    inputTokens: number;
    outputTokens: number;
    customerCostUsd: string;
    providerCostUsd: string;
    grossMarginUsd: string;
    latencyMs: number;
  },
): Promise<void> {
  await prisma.inferenceRequest
    .create({
      data: {
        userId: authed.user.id,
        apiKeyId: authed.apiKey.id,
        model,
        provider,
        kind,
        inputTokens: m.inputTokens,
        outputTokens: m.outputTokens,
        customerCostUsd: m.customerCostUsd,
        providerCostUsd: m.providerCostUsd,
        grossMarginUsd: m.grossMarginUsd,
        latencyMs: m.latencyMs,
        status: "failed",
        errorMessage,
      },
    })
    .catch(() => {});
}
