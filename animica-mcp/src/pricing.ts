/**
 * Per-model pricing (USD per 1M tokens) for the price_quote tool and cost notes.
 * Animica undercuts hosted incumbents thanks to miner-powered inference. Tune via
 * ANIMICA_PRICING_JSON (a JSON map of model -> {in, out}) without code changes.
 */
export interface ModelPrice { in: number; out: number; } // USD per 1M tokens

const DEFAULT_PRICING: Record<string, ModelPrice> = {
  'anm-fast-8b': { in: 0.10, out: 0.30 },
  'anm-code-7b': { in: 0.15, out: 0.45 },
  'anm-pro-70b': { in: 0.60, out: 1.80 },
  'anm-flagship-moe': { in: 0.40, out: 1.20 },
  'anm-embed': { in: 0.02, out: 0.0 },
};

export function pricing(): Record<string, ModelPrice> {
  if (process.env.ANIMICA_PRICING_JSON) {
    try { return { ...DEFAULT_PRICING, ...JSON.parse(process.env.ANIMICA_PRICING_JSON) }; } catch { /* fall through */ }
  }
  return DEFAULT_PRICING;
}

export function quote(model: string, inputTokens: number, outputTokens: number) {
  const p = pricing()[model] || DEFAULT_PRICING['anm-fast-8b'];
  const cost = (inputTokens / 1e6) * p.in + (outputTokens / 1e6) * p.out;
  return {
    model,
    input_tokens: inputTokens,
    output_tokens: outputTokens,
    price_per_1m_input_usd: p.in,
    price_per_1m_output_usd: p.out,
    estimated_cost_usd: Number(cost.toFixed(6)),
  };
}

/** Rough token estimate (≈4 chars/token) for quick quotes without a tokenizer. */
export function estimateTokens(text: string): number {
  return Math.max(1, Math.ceil((text || '').length / 4));
}
