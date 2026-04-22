import { estimateTokenCount } from '@animica/aicf-shared';

export function runCustomAdapter(input: Record<string, unknown>) {
  const digest = JSON.stringify(input).slice(0, 200);

  return {
    output: {
      result: 'custom workload completed',
      digest,
      runtime: 'provider-custom'
    },
    usage: {
      inputTokens: estimateTokenCount(JSON.stringify(input)),
      outputTokens: estimateTokenCount(digest),
      latencyMs: 500,
      bytesIn: JSON.stringify(input).length,
      bytesOut: digest.length
    }
  };
}
