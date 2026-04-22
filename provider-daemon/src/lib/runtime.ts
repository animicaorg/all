import { createHash } from 'node:crypto';
import type { ContractJobRecord, JobRecord } from '@animica/aicf-shared';
import { runAgentAdapter } from '../adapters/agent.js';
import { runChatAdapter } from '../adapters/chat.js';
import { runCustomAdapter } from '../adapters/custom.js';
import { runEmbeddingAdapter } from '../adapters/embedding.js';
import { runTrainingAdapter } from '../adapters/training.js';

export function executeJob(job: JobRecord): {
  output: Record<string, unknown>;
  usage: {
    inputTokens?: number;
    outputTokens?: number;
    embeddingVectors?: number;
    latencyMs?: number;
    bytesIn?: number;
    bytesOut?: number;
  };
} {
  switch (job.request.class) {
    case 'chat_inference':
    case 'batch_inference':
    case 'evaluation':
      return runChatAdapter(job.request.input);
    case 'embedding_generation':
    case 'retrieval_indexing':
      return runEmbeddingAdapter(job.request.input);
    case 'fine_tuning_training':
      return runTrainingAdapter(job.request.input);
    case 'agent_task':
      return runAgentAdapter(job.request.input);
    case 'custom_compute':
      return runCustomAdapter(job.request.input);
    default:
      return runCustomAdapter(job.request.input);
  }
}

export function executeContractJob(job: ContractJobRecord): {
  output: Record<string, unknown>;
  usage: {
    inputTokens: number;
    outputTokens: number;
    embeddingVectors: number;
    latencyMs: number;
    bytesIn: number;
    bytesOut: number;
  };
  resultHash: string;
  resultRef: string;
  toolTraceRef?: string;
} {
  const syntheticInput = {
    input_ref_hash: job.inputRefHash,
    model: job.modelId,
    job_type: job.jobType,
    mode: job.mode
  };
  const base = (() => {
    switch (job.jobType) {
      case 'embedding':
        return runEmbeddingAdapter({ input: job.inputRefHash });
      case 'agent_task':
        return runAgentAdapter({ objective: `contract-job:${job.onchainJobId}` });
      case 'classification':
        return runCustomAdapter({ classify: job.inputRefHash, labels: ['positive', 'neutral', 'negative'] });
      case 'model_call':
        return runChatAdapter({
          messages: [
            { role: 'system', content: 'contract-driven model call' },
            { role: 'user', content: `input_ref_hash=${job.inputRefHash}` }
          ]
        });
      default:
        return runCustomAdapter(syntheticInput);
    }
  })();

  const output = {
    ...base.output,
    contractJobId: job.id,
    onchainJobId: job.onchainJobId
  };
  const serialized = JSON.stringify(output);
  const resultHash = `0x${createHash('sha256').update(serialized).digest('hex')}`;
  const resultRef = `aicf://results/${job.onchainJobId}/${resultHash.slice(2, 18)}.json`;

  return {
    output,
    usage: {
      inputTokens: base.usage.inputTokens ?? 0,
      outputTokens: base.usage.outputTokens ?? 0,
      embeddingVectors: 'embeddingVectors' in base.usage ? (base.usage.embeddingVectors ?? 0) : 0,
      latencyMs: base.usage.latencyMs ?? 0,
      bytesIn: base.usage.bytesIn ?? 0,
      bytesOut: base.usage.bytesOut ?? serialized.length
    },
    resultHash,
    resultRef,
    toolTraceRef: job.jobType === 'agent_task' ? `aicf://trace/${job.onchainJobId}` : undefined
  };
}
