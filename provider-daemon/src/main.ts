import 'dotenv/config';
import type { ContractJobRecord, JobRecord } from '@animica/aicf-shared';
import { ProviderApiClient } from './lib/apiClient.js';
import { executeContractJob, executeJob } from './lib/runtime.js';

const config = {
  baseUrl: process.env.AICF_API_BASE_URL ?? 'http://127.0.0.1:8099',
  providerId: process.env.AICF_PROVIDER_ID ?? '',
  providerToken: process.env.AICF_PROVIDER_TOKEN ?? '',
  nodeId: process.env.AICF_NODE_ID ?? '',
  heartbeatMs: Number(process.env.AICF_DAEMON_HEARTBEAT_MS ?? 3000),
  claimMs: Number(process.env.AICF_DAEMON_CLAIM_MS ?? 2000),
  maxJobsPerTick: Number(process.env.AICF_DAEMON_MAX_JOBS_PER_TICK ?? 3),
  runtimeName: process.env.AICF_DAEMON_RUNTIME_NAME ?? 'provider-local'
};

if (!config.providerId || !config.providerToken || !config.nodeId) {
  throw new Error('Missing daemon credentials: AICF_PROVIDER_ID / AICF_PROVIDER_TOKEN / AICF_NODE_ID');
}

const api = new ProviderApiClient({
  baseUrl: config.baseUrl,
  providerId: config.providerId,
  providerToken: config.providerToken,
  nodeId: config.nodeId
});

let inFlight = 0;

async function heartbeatLoop() {
  for (;;) {
    try {
      await api.heartbeat({
        currentLoad: Math.min(100, inFlight * 25),
        queueDepth: inFlight
      });
    } catch (error) {
      console.error('[provider-daemon] heartbeat error', error);
    }
    await new Promise((resolve) => setTimeout(resolve, config.heartbeatMs));
  }
}

async function execute(job: JobRecord) {
  const startedAt = Date.now();
  inFlight += 1;
  try {
    const result = await executeJob(job);
    const duration = Date.now() - startedAt;
    await api.submitResult(job.id, {
      output: {
        ...result.output,
        runtimeName: config.runtimeName
      },
      usage: {
        ...result.usage,
        latencyMs: duration
      }
    });
    console.log(`[provider-daemon] completed job=${job.id} class=${job.request.class} ms=${duration}`);
  } catch (error) {
    console.error(`[provider-daemon] failed job=${job.id}`, error);
    await api.submitFailure(job.id, error instanceof Error ? error.message : 'execution_failed');
  } finally {
    inFlight = Math.max(0, inFlight - 1);
  }
}

async function executeContract(job: ContractJobRecord) {
  const startedAt = Date.now();
  inFlight += 1;
  try {
    const result = executeContractJob(job);
    const duration = Date.now() - startedAt;
    const signature = `sig:${config.providerId}:${job.onchainJobId}:${result.resultHash.slice(2, 14)}`;
    await api.submitContractCommitment(job.id, {
      resultHash: result.resultHash,
      resultRef: result.resultRef,
      signature,
      modelRuntime: config.runtimeName,
      usage: {
        ...result.usage,
        latencyMs: Math.max(result.usage.latencyMs, duration)
      },
      toolTraceRef: result.toolTraceRef
    });
    if (!job.finalResultRef) {
      await api.submitContractReference(job.id, result.resultRef);
    }
    console.log(
      `[provider-daemon] completed contract-job=${job.id} onchain=${job.onchainJobId} mode=${job.mode} ms=${duration}`
    );
  } catch (error) {
    console.error(`[provider-daemon] failed contract-job=${job.id}`, error);
  } finally {
    inFlight = Math.max(0, inFlight - 1);
  }
}

async function claimLoop() {
  for (;;) {
    try {
      const jobs = await api.claimJobs(config.maxJobsPerTick);
      if (jobs.length > 0) {
        for (const job of jobs) {
          await execute(job);
        }
      }

      const contractJobs = await api.claimContractJobs(config.maxJobsPerTick);
      if (contractJobs.length > 0) {
        for (const job of contractJobs) {
          await executeContract(job);
        }
      }
    } catch (error) {
      console.error('[provider-daemon] claim loop error', error);
    }

    await new Promise((resolve) => setTimeout(resolve, config.claimMs));
  }
}

async function main() {
  console.log(
    `[provider-daemon] started runtime=${config.runtimeName} baseUrl=${config.baseUrl} provider=${config.providerId} node=${config.nodeId}`
  );
  await Promise.all([heartbeatLoop(), claimLoop()]);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
