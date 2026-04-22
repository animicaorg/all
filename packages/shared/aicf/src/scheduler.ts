import type { JobRecord, ProviderNode, ProviderProfile } from './types.js';

export type SchedulerInput = {
  job: JobRecord;
  providers: ProviderProfile[];
  nodes: ProviderNode[];
  minStakeAnmNanos: bigint;
};

export type ProviderMatch = {
  providerId: string;
  nodeId: string;
  score: number;
  reasons: string[];
};

export function scoreProviderNode(input: {
  job: JobRecord;
  provider: ProviderProfile;
  node: ProviderNode;
  minStakeAnmNanos: bigint;
}): ProviderMatch | null {
  const { job, provider, node, minStakeAnmNanos } = input;

  if (provider.state !== 'active' || node.state !== 'active') return null;
  if (BigInt(provider.stakeAnm) < minStakeAnmNanos) return null;

  const required = job.request.requiredHardware;
  if (required?.minGpuMemoryGb && node.capabilities.gpuMemoryGb < required.minGpuMemoryGb) return null;
  if (required?.minCpu && node.capabilities.cpus < required.minCpu) return null;
  if (required?.minRamGb && node.capabilities.ramGb < required.minRamGb) return null;

  const runtimeMap: Record<string, string> = {
    chat_inference: 'llm',
    embedding_generation: 'embedding',
    batch_inference: 'llm',
    fine_tuning_training: 'training',
    evaluation: 'llm',
    retrieval_indexing: 'embedding',
    agent_task: 'agent',
    custom_compute: 'custom'
  };

  const requiredRuntime = runtimeMap[job.request.class] ?? 'custom';
  if (node.capabilities.runtime !== requiredRuntime && node.capabilities.runtime !== 'custom') {
    return null;
  }

  let score = 0;
  const reasons: string[] = [];

  const reputationScore = Math.max(0, Math.min(100, provider.reputation));
  score += reputationScore * 3;
  reasons.push(`reputation:${reputationScore}`);

  const benchmarkScore = Math.max(0, Math.min(100, node.benchmark.score));
  score += benchmarkScore * 4;
  reasons.push(`benchmark:${benchmarkScore}`);

  const loadPenalty = Math.max(0, Math.min(100, node.currentLoad));
  score += (100 - loadPenalty) * 2;
  reasons.push(`load:${node.currentLoad}`);

  const queuePenalty = Math.max(0, node.queueDepth);
  score += Math.max(0, 100 - queuePenalty * 10);
  reasons.push(`queueDepth:${node.queueDepth}`);

  if (job.request.regionPreference && node.capabilities.region === job.request.regionPreference) {
    score += 50;
    reasons.push('region_match');
  }

  const failureRate = provider.totalJobsCompleted + provider.totalJobsFailed === 0
    ? 0
    : provider.totalJobsFailed / (provider.totalJobsCompleted + provider.totalJobsFailed);
  score += Math.round((1 - Math.min(1, failureRate)) * 100);
  reasons.push(`failureRate:${failureRate.toFixed(3)}`);

  return {
    providerId: provider.id,
    nodeId: node.id,
    score,
    reasons
  };
}

export function chooseProvider(input: SchedulerInput): ProviderMatch | null {
  const allMatches: ProviderMatch[] = [];

  for (const provider of input.providers) {
    const ownedNodes = input.nodes.filter((n) => n.providerId === provider.id);
    for (const node of ownedNodes) {
      const match = scoreProviderNode({
        job: input.job,
        provider,
        node,
        minStakeAnmNanos: input.minStakeAnmNanos
      });
      if (match) allMatches.push(match);
    }
  }

  if (allMatches.length === 0) return null;
  allMatches.sort((a, b) => b.score - a.score);
  return allMatches[0];
}
