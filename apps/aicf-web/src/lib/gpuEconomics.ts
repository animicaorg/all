import type { JobRecord, ProviderNode } from '@animica/aicf-shared';
import { clamp } from './anm';

export type NetworkCounts = {
  providers: number;
  nodes: number;
  jobs: number;
  contractJobs: number;
  agentTasks: number;
};

export type NetworkDemandSnapshot = {
  counts: NetworkCounts;
  demandIndex: number;
  demandMultiplier: number;
  demandLabel: 'idle' | 'normal' | 'busy' | 'surge';
  estimatedGpuSlots: number;
};

export type ProviderGpuStats = {
  totalNodes: number;
  activeNodes: number;
  totalGpus: number;
  totalGpuMemoryGb: number;
  averageLoadPercent: number;
  queueDepth: number;
  llmTokensPerSecond: number;
  embeddingVectorsPerSecond: number;
  trainingSamplesPerSecond: number;
  averageBenchmarkScore: number;
  strongestRegion: string;
  jobsRunning: number;
  jobsCompleted: number;
  jobsFailed: number;
};

function normalizeCounts(value: Record<string, unknown> | undefined): NetworkCounts {
  const read = (key: keyof NetworkCounts) => {
    const parsed = Number(value?.[key]);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : 0;
  };
  return {
    providers: read('providers'),
    nodes: read('nodes'),
    jobs: read('jobs'),
    contractJobs: read('contractJobs'),
    agentTasks: read('agentTasks')
  };
}

export function deriveNetworkDemand(statusPayload: Record<string, unknown> | null): NetworkDemandSnapshot {
  const health = (statusPayload?.health as Record<string, unknown> | undefined) ?? undefined;
  const counts = normalizeCounts((health?.counts as Record<string, unknown> | undefined) ?? undefined);

  const totalPendingUnits = counts.jobs + counts.contractJobs + counts.agentTasks;
  const estimatedGpuSlots = Math.max(1, counts.providers * 3 + counts.nodes * 2);
  const demandIndex = clamp(totalPendingUnits / estimatedGpuSlots, 0, 3.2);
  const demandMultiplier = Number((1 + demandIndex * 0.68).toFixed(2));

  let demandLabel: NetworkDemandSnapshot['demandLabel'] = 'idle';
  if (demandIndex >= 2.1) {
    demandLabel = 'surge';
  } else if (demandIndex >= 1.25) {
    demandLabel = 'busy';
  } else if (demandIndex >= 0.55) {
    demandLabel = 'normal';
  }

  return {
    counts,
    demandIndex,
    demandMultiplier,
    demandLabel,
    estimatedGpuSlots
  };
}

export function deriveProviderGpuStats(nodes: ProviderNode[], jobs: JobRecord[]): ProviderGpuStats {
  if (!nodes.length) {
    const running = jobs.filter((job) => job.status === 'running').length;
    const completed = jobs.filter((job) => job.status === 'completed').length;
    const failed = jobs.filter((job) => job.status === 'failed').length;
    return {
      totalNodes: 0,
      activeNodes: 0,
      totalGpus: 0,
      totalGpuMemoryGb: 0,
      averageLoadPercent: 0,
      queueDepth: 0,
      llmTokensPerSecond: 0,
      embeddingVectorsPerSecond: 0,
      trainingSamplesPerSecond: 0,
      averageBenchmarkScore: 0,
      strongestRegion: 'n/a',
      jobsRunning: running,
      jobsCompleted: completed,
      jobsFailed: failed
    };
  }

  const totals = nodes.reduce(
    (acc, node) => {
      acc.totalGpus += node.capabilities.gpus;
      acc.totalGpuMemoryGb += node.capabilities.gpuMemoryGb;
      acc.totalLoad += node.currentLoad;
      acc.totalQueue += node.queueDepth;
      acc.totalLlmTps += node.benchmark.llmTokensPerSecond;
      acc.totalEmbVps += node.benchmark.embeddingVectorsPerSecond;
      acc.totalTrainSps += node.benchmark.trainingSamplesPerSecond;
      acc.totalScore += node.benchmark.score;
      acc.regions.set(node.capabilities.region, (acc.regions.get(node.capabilities.region) ?? 0) + node.benchmark.score);
      return acc;
    },
    {
      totalGpus: 0,
      totalGpuMemoryGb: 0,
      totalLoad: 0,
      totalQueue: 0,
      totalLlmTps: 0,
      totalEmbVps: 0,
      totalTrainSps: 0,
      totalScore: 0,
      regions: new Map<string, number>()
    }
  );

  const strongestRegion = [...totals.regions.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? 'unknown';
  const running = jobs.filter((job) => job.status === 'running').length;
  const completed = jobs.filter((job) => job.status === 'completed').length;
  const failed = jobs.filter((job) => job.status === 'failed').length;

  return {
    totalNodes: nodes.length,
    activeNodes: nodes.filter((node) => node.state === 'active').length,
    totalGpus: totals.totalGpus,
    totalGpuMemoryGb: totals.totalGpuMemoryGb,
    averageLoadPercent: Number((totals.totalLoad / nodes.length).toFixed(1)),
    queueDepth: totals.totalQueue,
    llmTokensPerSecond: totals.totalLlmTps,
    embeddingVectorsPerSecond: totals.totalEmbVps,
    trainingSamplesPerSecond: totals.totalTrainSps,
    averageBenchmarkScore: Number((totals.totalScore / nodes.length).toFixed(1)),
    strongestRegion,
    jobsRunning: running,
    jobsCompleted: completed,
    jobsFailed: failed
  };
}

export function estimateHelperCostNanos(input: {
  promptTokens: number;
  expectedCompletionTokens: number;
  demandMultiplier: number;
}): bigint {
  const baseNanos = 220_000;
  const promptNanos = input.promptTokens * 95;
  const completionNanos = input.expectedCompletionTokens * 140;
  const demandAdjusted = Math.ceil((baseNanos + promptNanos + completionNanos) * input.demandMultiplier);
  return BigInt(Math.max(demandAdjusted, 120_000));
}
