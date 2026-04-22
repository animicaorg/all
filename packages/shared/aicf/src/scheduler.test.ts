import { describe, expect, it } from 'vitest';
import { chooseProvider } from './scheduler.js';
import type { JobRecord, ProviderNode, ProviderProfile } from './types.js';

function sampleJob(): JobRecord {
  return {
    id: 'job-1',
    projectId: 'proj-1',
    status: 'queued',
    attempts: 0,
    maxAttempts: 3,
    request: {
      class: 'chat_inference',
      model: 'aicf-chat-1',
      input: { messages: [{ role: 'user', content: 'hello' }] },
      timeoutSeconds: 60,
      replication: 1,
      verificationMode: 'sampled',
      outputMode: 'private',
      challengeWindowSeconds: 900,
      requiredHardware: {
        minGpuMemoryGb: 16,
        minCpu: 8,
        minRamGb: 32
      }
    },
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    usage: {
      inputTokens: 0,
      outputTokens: 0,
      embeddingVectors: 0,
      latencyMs: 0,
      bytesIn: 0,
      bytesOut: 0
    },
    budget: {
      maxAnmNanos: '1000',
      reservedAnmNanos: '1000',
      subsidyAnmNanos: '0'
    },
    settlement: {
      status: 'pending',
      providerRewardAnmNanos: '0',
      treasuryCutAnmNanos: '0',
      chargedAnmNanos: '0',
      refundedAnmNanos: '0'
    }
  };
}

describe('scheduler', () => {
  it('picks best provider by score and capability', () => {
    const job = sampleJob();

    const providers: ProviderProfile[] = [
      {
        id: 'provider-1',
        userId: 'u1',
        walletAddress: 'anm1',
        daemonTokenHash: 'h',
        state: 'active',
        reputation: 70,
        totalJobsCompleted: 20,
        totalJobsFailed: 2,
        stakeAnm: '2000',
        slashHistory: [],
        createdAt: new Date().toISOString()
      }
    ];

    const nodes: ProviderNode[] = [
      {
        id: 'node-1',
        providerId: 'provider-1',
        metadata: { name: 'node', machineType: 'A100', os: 'linux', labels: [] },
        capabilities: {
          runtime: 'llm',
          gpus: 1,
          gpuMemoryGb: 40,
          cpus: 16,
          ramGb: 64,
          region: 'eu-central',
          modelFamilies: ['aicf-chat-1']
        },
        benchmark: {
          llmTokensPerSecond: 90,
          embeddingVectorsPerSecond: 0,
          trainingSamplesPerSecond: 0,
          score: 88
        },
        state: 'active',
        lastHeartbeatAt: new Date().toISOString(),
        currentLoad: 20,
        queueDepth: 1
      }
    ];

    const selected = chooseProvider({
      job,
      providers,
      nodes,
      minStakeAnmNanos: 1000n
    });

    expect(selected?.providerId).toBe('provider-1');
    expect(selected?.nodeId).toBe('node-1');
  });
});
