import { describe, expect, it } from 'vitest';
import { buildTestApp } from './setup.js';

describe('scheduler + provider execution flow', () => {
  it('assigns queued jobs to provider nodes and settles rewards', () => {
    const { service } = buildTestApp();

    const dev = service.signup({
      email: 'dev2@animica.org',
      password: 'dev-password-123',
      role: 'developer'
    });
    const devUser = service.authenticateSession(dev.token);

    const project = service.createProject(devUser, {
      name: 'Training Project',
      slug: 'training-proj'
    });
    service.createFundingIntent(devUser, {
      projectId: project.id,
      amountAnm: '25000000000000'
    });

    const providerSignup = service.signup({
      email: 'provider@animica.org',
      password: 'provider-password-123',
      role: 'provider'
    });
    const providerUser = service.authenticateSession(providerSignup.token);

    const providerRegistration = service.registerProvider(providerUser, {
      walletAddress: 'anm1providerxyz',
      signature: '0xprovidersig',
      daemonPublicKey: 'provider-public-key'
    });

    const provider = service.authenticateProviderDaemon(providerRegistration.provider.id, providerRegistration.daemonToken);
    service.providerStake(provider, '2000000000000');

    const node = service.registerProviderNode(provider, {
      metadata: {
        name: 'Berlin-GPU-1',
        machineType: 'A100x1',
        os: 'ubuntu-24.04',
        labels: ['gpu', 'llm']
      },
      capabilities: {
        runtime: 'llm',
        gpus: 1,
        gpuMemoryGb: 40,
        cpus: 24,
        ramGb: 96,
        region: 'eu-central',
        modelFamilies: ['aicf-chat-1']
      },
      benchmark: {
        llmTokensPerSecond: 88,
        embeddingVectorsPerSecond: 0,
        trainingSamplesPerSecond: 0,
        score: 91
      }
    });

    const job = service.createAsyncJob(devUser, {
      projectId: project.id,
      maxBudgetAnmNanos: '1500000000',
      request: {
        class: 'chat_inference',
        model: 'aicf-chat-1',
        input: { messages: [{ role: 'user', content: 'hello provider network' }] },
        timeoutSeconds: 120,
        replication: 1,
        verificationMode: 'sampled',
        outputMode: 'private',
        challengeWindowSeconds: 900,
        regionPreference: 'eu-central',
        requiredHardware: {
          minGpuMemoryGb: 16,
          minCpu: 8,
          minRamGb: 32
        }
      }
    });

    const tick = service.schedulerTick();
    expect(tick.assigned.length).toBeGreaterThan(0);

    const claimed = service.providerClaimJobs(provider, node.id, 3);
    expect(claimed.length).toBeGreaterThan(0);

    const updated = service.providerSubmitResult(provider, node.id, job.id, {
      output: {
        content: 'provider executed this workload'
      },
      usage: {
        inputTokens: 60,
        outputTokens: 84,
        latencyMs: 640
      }
    });

    expect(updated.status).toBe('completed');

    const providerJobs = service.listProviderJobs(provider);
    expect(providerJobs.some((row) => row.id === job.id && row.status === 'completed')).toBe(true);

    const claimRewards = service.providerClaimRewards(provider);
    expect(BigInt(claimRewards.claimedAnmNanos)).toBeGreaterThan(0n);
  });
});
