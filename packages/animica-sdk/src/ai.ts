/**
 * AnimicaAI — minimal OpenAI-compatible client for the free Animica AI API.
 *
 * Base URL: https://animica.dev/v1 (keyless; rate-limited; no API key needed,
 * but one is accepted and sent as a Bearer token when provided).
 * Model ids include 'kimi-k3' (Kimi K2 Instruct served by the miner network).
 *
 * Supports models.list() and chat.completions.create(), with optional
 * streaming via server-sent events parsed with native fetch — zero deps.
 */

import {
  FetchLike,
  HttpError,
  HttpOptions,
  fetchWithTimeout,
  resolveFetch,
} from "./http.js";

export const DEFAULT_AI_BASE_URL = "https://animica.dev/v1";

// ---------------------------------------------------------------------------
// Types (OpenAI-compatible subset)
// ---------------------------------------------------------------------------

export interface AIModel {
  id: string;
  object: "model";
  owned_by?: string;
  description?: string;
  chain_tier?: string;
  serving?: boolean;
  [key: string]: unknown;
}

export interface AIModelList {
  object: "list";
  data: AIModel[];
  [key: string]: unknown;
}

export type ChatRole = "system" | "user" | "assistant" | "tool";

export interface ChatMessage {
  role: ChatRole;
  content: string | null;
  name?: string;
  [key: string]: unknown;
}

export interface ChatCompletionRequest {
  model: string;
  messages: ChatMessage[];
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string | string[];
  stream?: boolean;
  [key: string]: unknown;
}

export interface ChatCompletionChoice {
  index: number;
  message: ChatMessage;
  finish_reason: string | null;
  [key: string]: unknown;
}

export interface ChatCompletion {
  id: string;
  object: string;
  created?: number;
  model?: string;
  choices: ChatCompletionChoice[];
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

export interface ChatCompletionChunkChoice {
  index: number;
  delta: Partial<ChatMessage>;
  finish_reason: string | null;
  [key: string]: unknown;
}

export interface ChatCompletionChunk {
  id: string;
  object: string;
  created?: number;
  model?: string;
  choices: ChatCompletionChunkChoice[];
  [key: string]: unknown;
}

export interface AnimicaAIOptions extends HttpOptions {
  /** Base URL of the OpenAI-compatible API. Default: https://animica.dev/v1 */
  baseUrl?: string;
  /** Optional API key — not required on animica.dev; sent as Bearer if set. */
  apiKey?: string;
}

// ---------------------------------------------------------------------------
// SSE parsing (exported for testing)
// ---------------------------------------------------------------------------

/**
 * Parse a fetch Response body as an OpenAI-style SSE stream.
 * Yields the JSON payload of each `data:` event; stops at `[DONE]`.
 * Handles events split across arbitrary chunk boundaries and multi-line data.
 */
export async function* parseSseStream<T = unknown>(
  body: ReadableStream<Uint8Array>
): AsyncGenerator<T, void, undefined> {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // Events are separated by a blank line; tolerate \r\n line endings.
      let sep: number;
      while ((sep = buffer.search(/\r?\n\r?\n/)) !== -1) {
        const rawEvent = buffer.slice(0, sep);
        buffer = buffer.slice(sep).replace(/^\r?\n\r?\n/, "");
        const dataLines: string[] = [];
        for (const line of rawEvent.split(/\r?\n/)) {
          if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          // comment lines (":") and other fields (event:, id:) are ignored
        }
        if (dataLines.length === 0) continue;
        const data = dataLines.join("\n");
        if (data.trim() === "[DONE]") return;
        yield JSON.parse(data) as T;
      }
    }
    // Flush any trailing complete event that wasn't followed by a blank line.
    const tail = buffer.trim();
    if (tail) {
      for (const line of tail.split(/\r?\n/)) {
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).replace(/^ /, "");
        if (data.trim() === "[DONE]") return;
        yield JSON.parse(data) as T;
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* stream already closed */
    }
  }
}

// ---------------------------------------------------------------------------
// Client
// ---------------------------------------------------------------------------

export class AnimicaAI {
  readonly baseUrl: string;
  private readonly apiKey?: string;
  private readonly fetchImpl: FetchLike;
  private readonly timeoutMs: number;
  private readonly extraHeaders: Record<string, string>;

  readonly models: { list: () => Promise<AIModelList> };
  readonly chat: {
    completions: {
      /** Non-streaming by default; pass stream:true for an AsyncIterable of chunks. */
      create: {
        (
          req: ChatCompletionRequest & { stream?: false }
        ): Promise<ChatCompletion>;
        (
          req: ChatCompletionRequest & { stream: true }
        ): Promise<AsyncGenerator<ChatCompletionChunk, void, undefined>>;
      };
    };
  };

  constructor(opts: AnimicaAIOptions = {}) {
    this.baseUrl = (opts.baseUrl ?? DEFAULT_AI_BASE_URL).replace(/\/+$/, "");
    this.apiKey = opts.apiKey;
    this.fetchImpl = resolveFetch(opts.fetch);
    this.timeoutMs = opts.timeoutMs ?? 120_000;
    this.extraHeaders = opts.headers ?? {};

    // OpenAI-SDK-like surface
    this.models = { list: () => this.listModels() };
    const create = ((req: ChatCompletionRequest) =>
      req.stream
        ? this.createChatCompletionStream(req)
        : this.createChatCompletion(req)) as AnimicaAI["chat"]["completions"]["create"];
    this.chat = { completions: { create } };
  }

  private headers(json = true): Record<string, string> {
    const h: Record<string, string> = {
      accept: "application/json",
      ...this.extraHeaders,
    };
    if (json) h["content-type"] = "application/json";
    if (this.apiKey) h["authorization"] = `Bearer ${this.apiKey}`;
    return h;
  }

  async listModels(): Promise<AIModelList> {
    const url = `${this.baseUrl}/models`;
    const res = await fetchWithTimeout(
      this.fetchImpl,
      url,
      { method: "GET", headers: this.headers(false) },
      this.timeoutMs
    );
    if (!res.ok) throw new HttpError(res.status, url, await safeText(res));
    return (await res.json()) as AIModelList;
  }

  async createChatCompletion(
    req: ChatCompletionRequest
  ): Promise<ChatCompletion> {
    const url = `${this.baseUrl}/chat/completions`;
    const res = await fetchWithTimeout(
      this.fetchImpl,
      url,
      {
        method: "POST",
        headers: this.headers(),
        body: JSON.stringify({ ...req, stream: false }),
      },
      this.timeoutMs
    );
    if (!res.ok) throw new HttpError(res.status, url, await safeText(res));
    return (await res.json()) as ChatCompletion;
  }

  async createChatCompletionStream(
    req: ChatCompletionRequest
  ): Promise<AsyncGenerator<ChatCompletionChunk, void, undefined>> {
    const url = `${this.baseUrl}/chat/completions`;
    const res = await fetchWithTimeout(
      this.fetchImpl,
      url,
      {
        method: "POST",
        headers: { ...this.headers(), accept: "text/event-stream" },
        body: JSON.stringify({ ...req, stream: true }),
      },
      this.timeoutMs
    );
    if (!res.ok) throw new HttpError(res.status, url, await safeText(res));
    if (!res.body) {
      throw new HttpError(res.status, url, "response has no body stream");
    }
    return parseSseStream<ChatCompletionChunk>(res.body);
  }
}

async function safeText(res: Response): Promise<string | undefined> {
  try {
    return await res.text();
  } catch {
    return undefined;
  }
}
