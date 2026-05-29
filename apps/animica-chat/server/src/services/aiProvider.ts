// AI provider abstraction.
//
// The provider speaks OpenAI's /v1/chat/completions shape — that's the
// de-facto interface every modern hosted gateway and self-hosted runner
// (vLLM, Together, Groq, Anyscale, ollama --api-key, even Animica's own
// AICF gateway) implements. By going through this interface we avoid
// vendor-locking the rest of the app to one provider's quirks.
//
// Streaming uses the OpenAI SSE format: each chunk arrives as
//   data: {"choices":[{"delta":{"content":"..."}}], ...}
// terminated by a "data: [DONE]" sentinel. We parse the chunked stream
// manually so we don't need a fat SDK dependency.

import { env } from '../env';

export type ChatMessage = { role: 'system' | 'user' | 'assistant'; content: string };

export interface ChatRequest {
  model?: string;
  messages: ChatMessage[];
  temperature?: number;
  topP?: number;
  maxTokens?: number;
  stop?: string[];
  // Caller-controlled cancel token; closing it abandons the network read.
  signal?: AbortSignal;
}

export interface UsageBreakdown {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
}

export interface ChatResponse {
  content: string;
  model: string;
  usage?: UsageBreakdown;
  finishReason?: string;
}

export interface ChatStreamChunk {
  // Incremental text appended since the previous chunk.
  delta: string;
  done: boolean;
  finishReason?: string;
  // Some providers ship a usage block on the final chunk.
  usage?: UsageBreakdown;
}

export class AIProviderError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function assertConfigured(): void {
  if (!env.AI_API_KEY) {
    throw new AIProviderError(
      'AI provider is not configured. Set AI_API_KEY in the environment.',
      503,
      'no_api_key',
    );
  }
}

function buildBody(req: ChatRequest, stream: boolean) {
  return {
    model: req.model || env.AI_DEFAULT_MODEL,
    messages: req.messages,
    temperature: req.temperature ?? 0.7,
    top_p: req.topP ?? 1,
    max_tokens: Math.min(req.maxTokens ?? env.AI_MAX_TOKENS, env.AI_MAX_TOKENS),
    stop: req.stop && req.stop.length > 0 ? req.stop : undefined,
    stream,
  };
}

function authHeaders(): HeadersInit {
  return {
    Authorization: `Bearer ${env.AI_API_KEY}`,
    'Content-Type': 'application/json',
  };
}

export async function chatCompletion(req: ChatRequest): Promise<ChatResponse> {
  assertConfigured();
  const url = `${env.AI_BASE_URL}/chat/completions`;
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), env.AI_REQUEST_TIMEOUT_MS);
  // Compose caller cancel + timeout into one signal.
  const combined = req.signal
    ? linkSignals([req.signal, timeout.signal])
    : timeout.signal;
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(buildBody(req, false)),
      signal: combined,
    });
    if (!res.ok) {
      const body = await safeRead(res);
      throw new AIProviderError(
        `AI provider returned ${res.status}`,
        res.status,
        body,
      );
    }
    const json = (await res.json()) as Record<string, unknown>;
    const choices = (json as { choices?: Array<{ message?: { content?: string }; finish_reason?: string }> }).choices;
    const usage = (json as { usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number } }).usage;
    const content = choices?.[0]?.message?.content ?? '';
    return {
      content,
      model: (json as { model?: string }).model || env.AI_DEFAULT_MODEL,
      usage: usage
        ? {
            promptTokens: usage.prompt_tokens,
            completionTokens: usage.completion_tokens,
            totalTokens: usage.total_tokens,
          }
        : undefined,
      finishReason: choices?.[0]?.finish_reason,
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function* chatStream(
  req: ChatRequest,
): AsyncGenerator<ChatStreamChunk> {
  assertConfigured();
  const url = `${env.AI_BASE_URL}/chat/completions`;
  const timeout = new AbortController();
  const timer = setTimeout(() => timeout.abort(), env.AI_REQUEST_TIMEOUT_MS);
  const combined = req.signal
    ? linkSignals([req.signal, timeout.signal])
    : timeout.signal;
  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(buildBody(req, true)),
      signal: combined,
    });
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
  if (!res.ok || !res.body) {
    const body = await safeRead(res);
    clearTimeout(timer);
    throw new AIProviderError(
      `AI provider stream returned ${res.status}`,
      res.status,
      body,
    );
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalUsage: UsageBreakdown | undefined;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx: number;
      // SSE frames are separated by a blank line.
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const raw = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        for (const line of raw.split('\n')) {
          const trimmed = line.trim();
          if (!trimmed.startsWith('data:')) continue;
          const payload = trimmed.slice(5).trim();
          if (payload === '[DONE]') {
            yield { delta: '', done: true, usage: finalUsage };
            return;
          }
          if (!payload) continue;
          try {
            const j = JSON.parse(payload) as {
              choices?: Array<{ delta?: { content?: string }; finish_reason?: string }>;
              usage?: { prompt_tokens?: number; completion_tokens?: number; total_tokens?: number };
            };
            const delta = j.choices?.[0]?.delta?.content ?? '';
            const finishReason = j.choices?.[0]?.finish_reason ?? undefined;
            if (j.usage) {
              finalUsage = {
                promptTokens: j.usage.prompt_tokens,
                completionTokens: j.usage.completion_tokens,
                totalTokens: j.usage.total_tokens,
              };
            }
            if (delta) yield { delta, done: false, finishReason };
            if (finishReason) yield { delta: '', done: true, finishReason, usage: finalUsage };
          } catch {
            // Skip malformed lines rather than killing the stream.
          }
        }
      }
    }
    yield { delta: '', done: true, usage: finalUsage };
  } finally {
    clearTimeout(timer);
    try {
      reader.releaseLock();
    } catch {
      /* noop */
    }
  }
}

async function safeRead(res: Response): Promise<string> {
  try {
    return await res.text();
  } catch {
    return '';
  }
}

function linkSignals(signals: AbortSignal[]): AbortSignal {
  const ac = new AbortController();
  for (const s of signals) {
    if (s.aborted) {
      ac.abort();
      return ac.signal;
    }
    s.addEventListener('abort', () => ac.abort(), { once: true });
  }
  return ac.signal;
}
