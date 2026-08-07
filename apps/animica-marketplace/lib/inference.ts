import { config } from './config';

// Inference routing. Metered marketplace serving goes through the PAID pool /v1 (anm_live_ key) so
// the free treasury (~days of runway) is not drained. Free preview may use the keyless bridge.
// Media jobs (image/video/audio) submit to the AICF job kinds added in 8.0.0 (see lib/media.ts).

export interface ChatMessage { role: 'system' | 'user' | 'assistant'; content: string }

export interface ChatResult {
  text: string;
  tokensIn: number;
  tokensOut: number;
  model: string;
  receiptId?: string;
}

interface ChatOpts {
  model?: string;
  temperature?: number;
  maxTokens?: number;
  paid?: boolean; // true => pool /v1 with key; false => free bridge
  timeoutMs?: number;
}

export async function chat(messages: ChatMessage[], opts: ChatOpts = {}): Promise<ChatResult> {
  const paid = opts.paid ?? true;
  const base = paid ? config.poolV1 : config.freeV1;
  const model = opts.model ?? (paid ? 'anm-fast-8b' : 'kimi-k3');
  const headers: Record<string, string> = { 'content-type': 'application/json' };
  if (paid && config.poolKey) headers['authorization'] = `Bearer ${config.poolKey}`;

  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), opts.timeoutMs ?? 120_000);
  try {
    const res = await fetch(`${base}/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model,
        messages,
        temperature: opts.temperature ?? 0.7,
        max_tokens: opts.maxTokens ?? 1024,
        stream: false,
      }),
      signal: ctrl.signal,
    });
    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`inference ${res.status}: ${body.slice(0, 300)}`);
    }
    const data = await res.json();
    const text = data.choices?.[0]?.message?.content ?? '';
    const usage = data.usage ?? {};
    return {
      text,
      tokensIn: Number(usage.prompt_tokens ?? estimateTokens(messages.map((m) => m.content).join(' '))),
      tokensOut: Number(usage.completion_tokens ?? estimateTokens(text)),
      model: data.model ?? model,
      receiptId: res.headers.get('x-animica-receipt') ?? undefined,
    };
  } finally {
    clearTimeout(t);
  }
}

export function estimateTokens(s: string): number {
  return Math.max(1, Math.ceil(s.length / 4));
}
