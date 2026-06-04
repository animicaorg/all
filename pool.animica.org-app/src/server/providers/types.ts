// Provider abstraction for the AI compute broker.
//
// Every upstream — the built-in mock today, RunPod / Bittensor / Akash /
// OpenAI-compatible servers later — implements `Provider`. A provider is
// responsible ONLY for producing the completion plus its own cost and token
// counts. The *customer* price is computed by the router from the model
// catalog (src/server/ai/catalog.ts), never by the provider. This keeps
// supply (cost) and sell (price) cleanly separated.
//
// All money is a Decimal string (see src/lib/money.ts).

export type ProviderName =
  | "mock"
  | "anthropic"
  | "openai-compatible"
  | "runpod"
  | "akash"
  | "bittensor"
  | "animica-worker";

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
}

export interface ChatRequest {
  model: string;
  messages: ChatMessage[];
  maxTokens?: number;
  temperature?: number;
}

export interface EmbedRequest {
  model: string;
  input: string[];
}

export interface ChatProviderResponse {
  output: string;
  inputTokens: number;
  outputTokens: number;
  /** What this provider cost us to serve the call, in USD (Decimal string). */
  providerCostUsd: string;
  latencyMs: number;
}

export interface EmbedProviderResponse {
  embeddings: number[][];
  inputTokens: number;
  providerCostUsd: string;
  latencyMs: number;
}

export interface Provider {
  readonly name: ProviderName;
  /** Can this provider serve the given catalog model id? */
  supports(model: string): boolean;
  chat(req: ChatRequest): Promise<ChatProviderResponse>;
  embed(req: EmbedRequest): Promise<EmbedProviderResponse>;
}

/** Thrown when a provider can't serve a request and the router should fall over. */
export class ProviderError extends Error {
  constructor(
    public readonly provider: ProviderName,
    message: string,
  ) {
    super(message);
    this.name = "ProviderError";
  }
}
