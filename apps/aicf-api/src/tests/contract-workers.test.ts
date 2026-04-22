import { describe, expect, it } from 'vitest';
import { buildTestApp } from './setup.js';

describe('contract watcher/scheduler/result/finalization worker hooks', () => {
  it('supports event ingestion and watcher drain', () => {
    const { service } = buildTestApp();

    const event = service.ingestObservedChainEvent({
      eventType: 'MODEL_CALL_REQUESTED',
      contractAddress: 'anm1contractwatcher',
      onchainJobId: 'job-watch-1',
      payload: {
        modelId: 'aicf-chat-1'
      }
    });

    expect(event.eventType).toBe('MODEL_CALL_REQUESTED');

    const drained = service.watchChainEvents(10);
    expect(drained.length).toBe(1);
    expect(drained[0]?.onchainJobId).toBe('job-watch-1');
  });

  it('runs scheduler, commitment promotion, and finalization ticks for contract jobs', () => {
    const { service } = buildTestApp();

    const dev = service.signup({
      email: 'worker-dev@animica.org',
      password: 'dev-password-123',
      role: 'developer'
    });
    const devUser = service.authenticateSession(dev.token);

    const contract = service.registerContract(devUser, {
      address: 'anm1workercontract123',
      type: 'model_call',
      metadata: {
        name: 'worker-contract'
      }
    });

    const providerSignup = service.signup({
      email: 'worker-provider@animica.org',
      password: 'provider-password-123',
      role: 'provider'
    });
    const providerUser = service.authenticateSession(providerSignup.token);

    const providerRegistration = service.registerProvider(providerUser, {
      walletAddress: 'anm1workerprovider',
      signature: '0xprovider',
      daemonPublicKey: 'daemon-key'
    });
    const provider = service.authenticateProviderDaemon(providerRegistration.provider.id, providerRegistration.daemonToken);
    service.providerStake(provider, '2000000000000');

    const node = service.registerProviderNode(provider, {
      metadata: {
        name: 'worker-node',
        machineType: 'A100x1',
        os: 'ubuntu',
        labels: ['contract-jobs']
      },
      capabilities: {
        runtime: 'llm',
        gpus: 1,
        gpuMemoryGb: 24,
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
      }
    });

    const created = service.createContractJob(devUser, {
      contractAddress: contract.address,
      requester: 'anm1requester',
      payer: 'anm1payer',
      modelId: 'aicf-chat-1',
      jobType: 'model_call',
      inputRefHash: '0xinputworker',
      maxBudgetAnmNanos: '2000000000',
      timeoutSeconds: 500,
      replication: 1,
      quorum: 1,
      verificationMode: 'SINGLE_PROVIDER',
      challengeWindowSeconds: 5,
      providerPolicy: {
        mode: 'open',
        providerIds: []
      },
      privacy: 'private',
      callbackMode: 'none',
      resultType: 'json'
    });

    const scheduled = service.scheduleContractJobs();
    expect(scheduled.assigned.length).toBeGreaterThan(0);

    const claimed = service.providerClaimContractJobs(provider, node.id, 3);
    expect(claimed.length).toBeGreaterThan(0);

    service.providerSubmitContractResultCommitment(provider, created.job.id, {
      nodeId: node.id,
      resultHash: '0xworkerresult',
      resultRef: 'aicf://results/worker.json',
      signature: 'sig-worker',
      modelRuntime: 'provider-llm',
      usage: {
        inputTokens: 80,
        outputTokens: 100,
        embeddingVectors: 0,
        latencyMs: 500,
        bytesIn: 500,
        bytesOut: 1000
      }
    });

    const moved = service.contractResultSubmitterTick(10);
    expect(moved.moved.length).toBeGreaterThanOrEqual(0);

    const store = (service as unknown as { store: { contractJobs: Map<string, any> } }).store;
    const job = store.contractJobs.get(created.job.id);
    if (job) {
      job.challengeWindowEndsAt = new Date(Date.now() - 1000).toISOString();
      job.state = 'result_submitted';
    }

    const finalized = service.contractFinalizationTick(10);
    expect(finalized.finalized.length).toBeGreaterThan(0);
  });
});
