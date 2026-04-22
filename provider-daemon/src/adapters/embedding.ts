import { deterministicPseudoEmbedding, estimateTokenCount } from '@animica/aicf-shared';

export function runEmbeddingAdapter(input: Record<string, unknown>) {
  const raw = input.input;
  const items = Array.isArray(raw) ? raw.map(String) : [String(raw ?? '')];
  const vectors = items.map((text) => deterministicPseudoEmbedding(text, 64));

  return {
    output: {
      vectors,
      dimensions: 64,
      count: vectors.length,
      runtime: 'provider-embedding'
    },
    usage: {
      inputTokens: items.reduce((sum, value) => sum + estimateTokenCount(value), 0),
      outputTokens: 0,
      embeddingVectors: vectors.length,
      latencyMs: 260,
      bytesIn: JSON.stringify(input).length,
      bytesOut: JSON.stringify(vectors).length
    }
  };
}
