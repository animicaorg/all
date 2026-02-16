import { generateContractCompletion, type LlmResponse } from "@/src/services/llmClient";

export type ModalChatResponse = LlmResponse;

export class ModalClientError extends Error {
  constructor(message: string, public readonly status: number, public readonly debug?: unknown) {
    super(message);
  }
}

export async function callModalChat(input: { prompt: string; context?: unknown; mode: string }): Promise<ModalChatResponse> {
  return generateContractCompletion({
    prompt: input.prompt,
    mode: input.mode,
    context: (input.context ?? {}) as Record<string, unknown>
  });
}
