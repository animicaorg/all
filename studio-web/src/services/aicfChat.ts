/**
 * aicfChat — thin JSON-RPC wrapper for the chat-side AICF flow.
 *
 * Mirrors the python agent_runtime.aicf_client semantics:
 *   estimateCost → user signs payment → submit → stream → settle
 *
 * Endpoint defaults to whatever the user's connected wallet considers its
 * active node RPC. Override via VITE_AICF_RPC_URL at build time or by
 * passing rpcUrl into makeAicfChat().
 */

export type ChatJobSpec = {
  prompt: string;
  tierPreferred?: "tiny" | "small" | "flagship" | "large";
  maxOutputTokens?: number;
  temperature?: number;
  topP?: number;
  history?: Array<{ role: "user" | "assistant"; content: string }>;
};

export type ChatCostQuote = {
  costAnimica: number;
  latencyMs: number;
  tier: string;
  providers: number;
};

export type ChatJobSubmit = {
  jobId: string;
  acceptedTier: string;
  expectedProviderId: string;
  paymentTxHash: string;
};

export type ChatChunk = {
  text: string;
  isFinal: boolean;
  tokenCount?: number;
};

export type ChatSettlement = {
  jobId: string;
  text: string;
  actualCostAnimica: number;
  providerId: string;
  latencyMs: number;
};

let nextRpcId = 1;

async function rpc<T>(url: string, method: string, params: unknown): Promise<T> {
  const r = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: nextRpcId++, method, params }),
  });
  if (!r.ok) {
    throw new Error(`AICF RPC ${method} HTTP ${r.status}`);
  }
  const body = (await r.json()) as {
    error?: { code: number | string; message: string; data?: unknown };
    result?: T;
  };
  if (body.error) {
    const err = new Error(`AICF ${body.error.code}: ${body.error.message}`);
    (err as Error & { data?: unknown }).data = body.error.data;
    throw err;
  }
  return body.result as T;
}

export type AicfChatClient = ReturnType<typeof makeAicfChat>;

export function makeAicfChat(rpcUrl: string) {
  if (!rpcUrl) throw new Error("aicfChat: rpcUrl required");

  return {
    estimateCost: (spec: ChatJobSpec) =>
      rpc<ChatCostQuote>(rpcUrl, "aicf.estimateJobCost", {
        prompt_tokens: Math.max(1, Math.floor(spec.prompt.length / 4)),
        max_output_tokens: spec.maxOutputTokens ?? 1024,
        tier_preferred: spec.tierPreferred,
        job_kind: "AI",
      }).then((raw) => ({
        costAnimica: Number((raw as { cost_animica?: number }).cost_animica ?? 0),
        latencyMs: Number((raw as { latency_ms?: number }).latency_ms ?? 0),
        tier: String((raw as { tier?: string }).tier ?? spec.tierPreferred ?? "small"),
        providers: Number((raw as { providers?: number }).providers ?? 0),
      })),

    submit: (spec: ChatJobSpec, paymentTxHash: string) =>
      rpc<{
        job_id: string;
        accepted_tier: string;
        provider_id: string;
      }>(rpcUrl, "aicf.submitInferenceJob", {
        spec: {
          prompt: spec.prompt,
          tier_preferred: spec.tierPreferred,
          job_kind: "AI",
          max_output_tokens: spec.maxOutputTokens ?? 1024,
          temperature: spec.temperature ?? 0.2,
          top_p: spec.topP ?? 0.95,
          metadata: { history_len: spec.history?.length ?? 0 },
        },
        payment: { txn_hash: paymentTxHash },
      }).then((raw) => ({
        jobId: raw.job_id,
        acceptedTier: raw.accepted_tier,
        expectedProviderId: raw.provider_id,
        paymentTxHash,
      })),

    streamOnce: (jobId: string, cursor: number) =>
      rpc<{
        text: string;
        final: boolean;
        next_cursor?: number;
        token_count?: number;
      }>(rpcUrl, "aicf.streamJob", { job_id: jobId, cursor }).then((raw) => ({
        text: raw.text ?? "",
        isFinal: !!raw.final,
        nextCursor: raw.next_cursor ?? cursor + (raw.text?.length ?? 0),
        tokenCount: raw.token_count,
      })),

    settle: (jobId: string) =>
      rpc<{
        text: string;
        cost_animica: number;
        provider_id: string;
        latency_ms: number;
      }>(rpcUrl, "aicf.settleJob", { job_id: jobId }).then((raw) => ({
        jobId,
        text: raw.text ?? "",
        actualCostAnimica: Number(raw.cost_animica ?? 0),
        providerId: raw.provider_id ?? "",
        latencyMs: Number(raw.latency_ms ?? 0),
      })),

    /**
     * Convenience: run a full turn end-to-end. Caller supplies a payer
     * function that takes (cost, recipient) and returns the txn hash.
     */
    runTurn: async (
      spec: ChatJobSpec,
      pay: (costAnimica: number, recipient: string) => Promise<string>,
      onChunk?: (c: ChatChunk) => void,
      treasuryAddress = "aicf-treasury",
      pollIntervalMs = 250,
      timeoutMs = 60_000,
    ): Promise<ChatSettlement> => {
      const quote = await rpc<{
        cost_animica?: number;
        latency_ms?: number;
        tier?: string;
        providers?: number;
      }>(rpcUrl, "aicf.estimateJobCost", {
        prompt_tokens: Math.max(1, Math.floor(spec.prompt.length / 4)),
        max_output_tokens: spec.maxOutputTokens ?? 1024,
        tier_preferred: spec.tierPreferred,
        job_kind: "AI",
      });
      const cost = Number(quote.cost_animica ?? 0);
      const txHash = await pay(cost, treasuryAddress);
      const submitted = await rpc<{
        job_id: string;
        accepted_tier: string;
        provider_id: string;
      }>(rpcUrl, "aicf.submitInferenceJob", {
        spec: {
          prompt: spec.prompt,
          tier_preferred: spec.tierPreferred,
          job_kind: "AI",
          max_output_tokens: spec.maxOutputTokens ?? 1024,
          temperature: spec.temperature ?? 0.2,
          top_p: spec.topP ?? 0.95,
          metadata: { history_len: spec.history?.length ?? 0 },
        },
        payment: { txn_hash: txHash },
      });
      let cursor = 0;
      const deadline = Date.now() + timeoutMs;
      while (Date.now() < deadline) {
        const chunk = await rpc<{
          text: string;
          final: boolean;
          next_cursor?: number;
        }>(rpcUrl, "aicf.streamJob", { job_id: submitted.job_id, cursor });
        if (chunk.text) onChunk?.({ text: chunk.text, isFinal: false });
        cursor = chunk.next_cursor ?? cursor + (chunk.text?.length ?? 0);
        if (chunk.final) break;
        await new Promise((r) => setTimeout(r, pollIntervalMs));
      }
      const settle = await rpc<{
        text: string;
        cost_animica: number;
        provider_id: string;
        latency_ms: number;
      }>(rpcUrl, "aicf.settleJob", { job_id: submitted.job_id });
      onChunk?.({ text: "", isFinal: true });
      return {
        jobId: submitted.job_id,
        text: settle.text ?? "",
        actualCostAnimica: Number(settle.cost_animica ?? 0),
        providerId: settle.provider_id ?? "",
        latencyMs: Number(settle.latency_ms ?? 0),
      };
    },
  };
}
