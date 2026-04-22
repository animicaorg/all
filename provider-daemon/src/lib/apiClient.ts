import type { ContractJobRecord, JobRecord } from '@animica/aicf-shared';

export type DaemonConfig = {
  baseUrl: string;
  providerId: string;
  providerToken: string;
  nodeId: string;
};

export class ProviderApiClient {
  constructor(private readonly config: DaemonConfig) {}

  private headers(extra?: Record<string, string>): Record<string, string> {
    return {
      'content-type': 'application/json',
      'x-provider-id': this.config.providerId,
      'x-provider-token': this.config.providerToken,
      ...(extra ?? {})
    };
  }

  async heartbeat(input: { currentLoad: number; queueDepth: number }) {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/nodes/${this.config.nodeId}/heartbeat`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify(input)
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`heartbeat failed: ${response.status} ${text}`);
    }

    return (await response.json()) as { node: unknown };
  }

  async claimJobs(limit: number): Promise<JobRecord[]> {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/jobs/claim`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        nodeId: this.config.nodeId,
        limit
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`claim jobs failed: ${response.status} ${text}`);
    }

    const payload = (await response.json()) as { jobs?: JobRecord[] };
    return payload.jobs ?? [];
  }

  async submitResult(jobId: string, input: {
    output: Record<string, unknown>;
    usage: {
      inputTokens?: number;
      outputTokens?: number;
      embeddingVectors?: number;
      latencyMs?: number;
      bytesIn?: number;
      bytesOut?: number;
    };
  }) {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/jobs/${jobId}/result`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        nodeId: this.config.nodeId,
        output: input.output,
        usage: input.usage
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`submit result failed: ${response.status} ${text}`);
    }

    return await response.json();
  }

  async submitFailure(jobId: string, reason: string) {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/jobs/${jobId}/fail`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        nodeId: this.config.nodeId,
        reason
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`submit failure failed: ${response.status} ${text}`);
    }

    return await response.json();
  }

  async claimContractJobs(limit: number): Promise<ContractJobRecord[]> {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/contract-jobs/claim`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        nodeId: this.config.nodeId,
        limit
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`claim contract jobs failed: ${response.status} ${text}`);
    }

    const payload = (await response.json()) as { jobs?: ContractJobRecord[] };
    return payload.jobs ?? [];
  }

  async submitContractCommitment(
    jobId: string,
    input: {
      resultHash: string;
      resultRef?: string;
      signature: string;
      modelRuntime: string;
      usage: {
        inputTokens: number;
        outputTokens: number;
        embeddingVectors: number;
        latencyMs: number;
        bytesIn: number;
        bytesOut: number;
      };
      toolTraceRef?: string;
      verifierRef?: string;
      quorumGroup?: string;
    }
  ) {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/contract-jobs/${jobId}/commitment`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        nodeId: this.config.nodeId,
        resultHash: input.resultHash,
        resultRef: input.resultRef,
        signature: input.signature,
        modelRuntime: input.modelRuntime,
        usage: input.usage,
        toolTraceRef: input.toolTraceRef,
        verifierRef: input.verifierRef,
        quorumGroup: input.quorumGroup
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`submit contract commitment failed: ${response.status} ${text}`);
    }

    return await response.json();
  }

  async submitContractReference(jobId: string, resultRef: string) {
    const response = await fetch(`${this.config.baseUrl}/provider/daemon/contract-jobs/${jobId}/reference`, {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({
        resultRef
      })
    });

    if (!response.ok) {
      const text = await response.text();
      throw new Error(`submit contract reference failed: ${response.status} ${text}`);
    }

    return await response.json();
  }
}
