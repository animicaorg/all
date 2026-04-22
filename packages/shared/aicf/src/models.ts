import type { ModelDefinition } from './types.js';

export const DEFAULT_MODELS: ModelDefinition[] = [
  {
    id: 'aicf-chat-1',
    name: 'aicf-chat-1',
    version: '2026-04-22',
    type: 'chat',
    status: 'active',
    defaultProviderRouting: 'provider_network',
    runtimeRequirements: {
      minGpuMemoryGb: 16,
      minCpu: 8,
      minRamGb: 32,
      runtimes: ['vllm', 'llama.cpp', 'first-party-chat']
    },
    pricing: {
      model: 'aicf-chat-1',
      inputTokenAnmNanos: '12000',
      outputTokenAnmNanos: '18000',
      embeddingVectorAnmNanos: '0',
      requestBaseAnmNanos: '150000',
      providerShareBps: 8500,
      treasuryShareBps: 1500
    }
  },
  {
    id: 'aicf-embed-1',
    name: 'aicf-embed-1',
    version: '2026-04-22',
    type: 'embedding',
    status: 'active',
    defaultProviderRouting: 'provider_network',
    runtimeRequirements: {
      minGpuMemoryGb: 8,
      minCpu: 4,
      minRamGb: 16,
      runtimes: ['sentence-transformers', 'first-party-embed']
    },
    pricing: {
      model: 'aicf-embed-1',
      inputTokenAnmNanos: '8000',
      outputTokenAnmNanos: '0',
      embeddingVectorAnmNanos: '50000',
      requestBaseAnmNanos: '100000',
      providerShareBps: 8200,
      treasuryShareBps: 1800
    }
  }
];

export function getModelDefinition(modelName: string): ModelDefinition {
  const match = DEFAULT_MODELS.find((model) => model.name === modelName);
  if (!match) {
    throw new Error(`Unknown model '${modelName}'`);
  }
  return match;
}
