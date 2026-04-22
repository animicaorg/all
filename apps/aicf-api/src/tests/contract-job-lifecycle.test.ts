import { describe, expect, it } from 'vitest';
import { buildTestApp } from './setup.js';

describe('contract-driven model call lifecycle', () => {
  it('creates, assigns, commits, accepts, and finalizes callback-accept job', () => {
    const { service } = buildTestApp();

    const dev = service.signup({
      email: 'contract-dev@animica.org',
      password: 'dev-password-123',
      role: 'developer'
    });
    const devUser = service.authenticateSession(dev.token);

    const contract = service.registerContract(devUser, {
      address: 'anm1contractabc12345',
      type: 'model_call',
      metadata: {
        name: 'summarize_document_contract'
      }
    });

    const created = service.createContractJob(devUser, {
      contractAddress: contract.address,
      requester: 'anm1requesterabc',
      payer: 'anm1payerabc',
      modelId: 'aicf-chat-1',
      jobType: 'model_call',
      inputRefHash: '0xinputhashabc',
      maxBudgetAnmNanos: '4000000000',
      timeoutSeconds: 600,
      replication: 1,
      quorum: 1,
      verificationMode: 'CALLBACK_ACCEPT',
      challengeWindowSeconds: 300,
      providerPolicy: {
        mode: 'open',
        providerIds: []
      },
      privacy: 'private',
      callbackMode: 'requester_accept',
      resultType: 'json'
    });

    const providerSignup = service.signup({
      email: 'contract-provider@animica.org',
      password: 'provider-password-123',
      role: 'provider'
    });
    const providerUser = service.authenticateSession(providerSignup.token);

    const providerRegistration = service.registerProvider(providerUser, {
      walletAddress: 'anm1providercontract',
      signature: '0xprovidersig',
      daemonPublicKey: 'provider-daemon-key'
    });

    const provider = service.authenticateProviderDaemon(providerRegistration.provider.id, providerRegistration.daemonToken);
    service.providerStake(provider, '3000000000000');

    const node = service.registerProviderNode(provider, {
      metadata: {
        name: 'Contract-Node-1',
        machineType: 'A100x1',
        os: 'ubuntu-24.04',
        labels: ['contract-jobs']
      },
      capabilities: {
        runtime: 'llm',
        gpus: 1,
        gpuMemoryGb: 40,
        cpus: 24,
        ramGb: 128,
        region: 'eu-central',
        modelFamilies: ['aicf-chat-1']
      },
      benchmark: {
        llmTokensPerSecond: 95,
        embeddingVectorsPerSecond: 0,
        trainingSamplesPerSecond: 0,
        score: 93
      }
    });

    const scheduled = service.scheduleContractJobs();
    expect(scheduled.assigned.length).toBeGreaterThan(0);

    const claimed = service.providerClaimContractJobs(provider, node.id, 5);
    expect(claimed.some((job) => job.id === created.job.id)).toBe(true);

    const committed = service.providerSubmitContractResultCommitment(provider, created.job.id, {
      nodeId: node.id,
      resultHash: '0xresulthashabc123',
      resultRef: 'aicf://results/job-1.json',
      signature: 'sig:provider:job',
      modelRuntime: 'provider-llm',
      usage: {
        inputTokens: 120,
        outputTokens: 160,
        embeddingVectors: 0,
        latencyMs: 780,
        bytesIn: 800,
        bytesOut: 1400
      }
    });

    expect(committed.job.state === 'result_submitted' || committed.job.state === 'accepted').toBe(true);

    const accepted = service.acceptContractResult(devUser, created.job.id);
    expect(accepted.state).toBe('accepted');

    const finalized = service.finalizeContractJob(devUser, created.job.id);
    expect(finalized.job.state).toBe('finalized_paid');
    expect(BigInt(finalized.job.paidAnmNanos)).toBeGreaterThanOrEqual(0n);
  });

  it('opens dispute and resolves with requester refund path', async () => {
    const { service } = buildTestApp();

    const dev = service.signup({
      email: 'contract-dev-2@animica.org',
      password: 'dev-password-123',
      role: 'developer'
    });
    const devUser = service.authenticateSession(dev.token);

    const admin = service.login({
      email: 'admin@test.animica.org',
      password: 'admin-password-test'
    });
    const adminUser = service.authenticateSession(admin.token);

    const contract = service.registerContract(devUser, {
      address: 'anm1contractrefund1234',
      type: 'model_call',
      metadata: { name: 'classify_records_contract' }
    });

    const created = service.createContractJob(devUser, {
      contractAddress: contract.address,
      requester: 'anm1requesterabc',
      payer: 'anm1payerabc',
      modelId: 'aicf-chat-1',
      jobType: 'model_call',
      inputRefHash: '0xinputhashrefund',
      maxBudgetAnmNanos: '3000000000',
      timeoutSeconds: 1,
      replication: 1,
      quorum: 1,
      verificationMode: 'SINGLE_PROVIDER',
      challengeWindowSeconds: 30,
      providerPolicy: {
        mode: 'open',
        providerIds: []
      },
      privacy: 'private',
      callbackMode: 'none',
      resultType: 'json'
    });

    const disputeOpen = service.openContractDispute(devUser, {
      jobId: created.job.id,
      reasonCode: 'OUTPUT_POLICY_MISMATCH'
    });
    expect(disputeOpen.job.state).toBe('challenged');

    const resolved = service.resolveContractDispute(adminUser, {
      disputeId: disputeOpen.dispute.id,
      action: 'refund_requester',
      note: 'failed verifier checks'
    });
    expect(resolved.job.state).toBe('finalized_refunded');

    const expiring = service.createContractJob(devUser, {
      contractAddress: contract.address,
      requester: 'anm1requesterabc',
      payer: 'anm1payerabc',
      modelId: 'aicf-chat-1',
      jobType: 'model_call',
      inputRefHash: '0xinputhashexpire',
      maxBudgetAnmNanos: '1500000000',
      timeoutSeconds: 1,
      replication: 1,
      quorum: 1,
      verificationMode: 'SINGLE_PROVIDER',
      challengeWindowSeconds: 30,
      providerPolicy: {
        mode: 'open',
        providerIds: []
      },
      privacy: 'private',
      callbackMode: 'none',
      resultType: 'json'
    });

    await new Promise((resolve) => setTimeout(resolve, 1100));
    const expired = service.refundContractJobIfExpired(devUser, expiring.job.id);
    expect(expired.state).toBe('expired');
  });
});
