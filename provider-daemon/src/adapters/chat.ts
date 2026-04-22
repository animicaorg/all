import { estimateTokenCount } from '@animica/aicf-shared';

export function runChatAdapter(input: Record<string, unknown>) {
  const messages = (input.messages as Array<{ role: string; content: string }>) ?? [];
  const merged = messages.map((msg) => `${msg.role}: ${msg.content}`).join('\n');
  const response = `provider chat runtime output | ${merged.slice(0, 640)}`;

  return {
    output: {
      content: response,
      runtime: 'provider-llm'
    },
    usage: {
      inputTokens: estimateTokenCount(JSON.stringify(input)),
      outputTokens: estimateTokenCount(response),
      latencyMs: 420,
      bytesIn: JSON.stringify(input).length,
      bytesOut: response.length
    }
  };
}
