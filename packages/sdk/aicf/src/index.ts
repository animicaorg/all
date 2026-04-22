import type {
  AgentTaskRecord,
  ContractDisputeRecord,
  ContractJobRecord,
  ContractRecord,
  JobRecord,
  ModelDefinition,
  Project,
  UsageRecord
} from '@animica/aicf-shared';

export type AicfSdkOptions = {
  baseUrl: string;
  apiKey?: string;
  bearerToken?: string;
};

export type ChatMessage = {
  role: 'system' | 'user' | 'assistant';
  content: string;
};

export class AicfClient {
  constructor(private readonly options: AicfSdkOptions) {}

  private async request<T>(path: string, init: RequestInit = {}, authOverride?: { apiKey?: string; bearerToken?: string }): Promise<T> {
    const headers = new Headers(init.headers ?? {});
    headers.set('Content-Type', 'application/json');

    const apiKey = authOverride?.apiKey ?? this.options.apiKey;
    const bearerToken = authOverride?.bearerToken ?? this.options.bearerToken;

    if (apiKey) headers.set('Authorization', `Bearer ${apiKey}`);
    if (bearerToken) headers.set('x-session-token', bearerToken);

    const response = await fetch(`${this.options.baseUrl}${path}`, {
      ...init,
      headers
    });

    if (!response.ok) {
      const payload = await response.text();
      throw new Error(`AICF request failed ${response.status}: ${payload}`);
    }

    return (await response.json()) as T;
  }

  signup(input: { email: string; password: string; role?: 'developer' | 'provider' }) {
    return this.request<{ userId: string; token: string }>('/auth/signup', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  login(input: { email: string; password: string }) {
    return this.request<{ userId: string; token: string; role: string }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  listModels() {
    return this.request<{ models: ModelDefinition[] }>('/models');
  }

  createProject(token: string, input: { name: string; slug: string; description?: string }) {
    return this.request<{ project: Project }>(
      '/projects',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  listProjects(token: string) {
    return this.request<{ projects: Project[] }>('/projects', {}, { bearerToken: token });
  }

  createApiKey(token: string, projectId: string, input: { name: string; scopes: string[] }) {
    return this.request<{ keyId: string; token: string; prefix: string }>(
      `/projects/${projectId}/api-keys`,
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  chatCompletions(input: {
    model: string;
    project_id?: string;
    messages: ChatMessage[];
    max_tokens?: number;
    stream?: boolean;
    metadata?: Record<string, unknown>;
  }) {
    return this.request<Record<string, unknown>>('/v1/chat/completions', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  embeddings(input: {
    model: string;
    project_id?: string;
    input: string | string[];
    dimensions?: number;
  }) {
    return this.request<Record<string, unknown>>('/v1/embeddings', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  createJob(token: string, input: Record<string, unknown>) {
    return this.request<{ job: JobRecord }>(
      '/jobs',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  listJobs(token: string, projectId?: string) {
    const qp = new URLSearchParams();
    if (projectId) qp.set('projectId', projectId);
    return this.request<{ jobs: JobRecord[] }>(`/jobs?${qp.toString()}`, {}, { bearerToken: token });
  }

  listUsage(token: string, projectId?: string) {
    const qp = new URLSearchParams();
    if (projectId) qp.set('projectId', projectId);
    return this.request<{ usage: UsageRecord[] }>(`/usage?${qp.toString()}`, {}, { bearerToken: token });
  }

  registerProvider(token: string, input: {
    walletAddress: string;
    signature: string;
    daemonPublicKey: string;
  }) {
    return this.request<{ providerId: string; daemonToken: string }>(
      '/provider/register',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  listContracts(token: string) {
    return this.request<{ contracts: ContractRecord[] }>('/contracts', {}, { bearerToken: token });
  }

  registerContract(
    token: string,
    input: {
      address: string;
      type: 'model_call' | 'agent_task' | 'ai_escrow' | 'custom';
      metadata: { name: string; description?: string; tags?: string[]; abiRef?: string; sourceRef?: string };
    }
  ) {
    return this.request<{ contract: ContractRecord }>(
      '/contracts',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  listContractJobs(
    token: string,
    filter?: { contractAddress?: string; state?: string; modelId?: string; providerId?: string; disputeStatus?: string }
  ) {
    const qp = new URLSearchParams();
    if (filter?.contractAddress) qp.set('contractAddress', filter.contractAddress);
    if (filter?.state) qp.set('state', filter.state);
    if (filter?.modelId) qp.set('modelId', filter.modelId);
    if (filter?.providerId) qp.set('providerId', filter.providerId);
    if (filter?.disputeStatus) qp.set('disputeStatus', filter.disputeStatus);
    return this.request<{ jobs: ContractJobRecord[] }>(`/contract-jobs?${qp.toString()}`, {}, { bearerToken: token });
  }

  createContractJob(token: string, input: Record<string, unknown>) {
    return this.request<{ job: ContractJobRecord; contractCalls: Record<string, unknown> }>(
      '/contract-jobs',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  getContractJob(token: string, jobId: string) {
    return this.request<{
      job: ContractJobRecord;
      commitments: Array<Record<string, unknown>>;
      assignments: Array<Record<string, unknown>>;
      escrowEvents: Array<Record<string, unknown>>;
    }>(`/contract-jobs/${jobId}`, {}, { bearerToken: token });
  }

  acceptContractJob(token: string, jobId: string, acceptedHash?: string) {
    return this.request<{ job: ContractJobRecord }>(
      `/contract-jobs/${jobId}/accept`,
      {
        method: 'POST',
        body: JSON.stringify({ acceptedHash })
      },
      { bearerToken: token }
    );
  }

  challengeContractJob(token: string, jobId: string, reasonCode: string, evidenceRef?: string) {
    return this.request<{ job: ContractJobRecord; dispute: ContractDisputeRecord }>(
      `/contract-jobs/${jobId}/disputes`,
      {
        method: 'POST',
        body: JSON.stringify({ reasonCode, evidenceRef })
      },
      { bearerToken: token }
    );
  }

  finalizeContractJob(token: string, jobId: string) {
    return this.request<{ job: ContractJobRecord; settlement: Record<string, unknown> }>(
      `/contract-jobs/${jobId}/finalize`,
      {
        method: 'POST',
        body: '{}'
      },
      { bearerToken: token }
    );
  }

  listAgentTasks(token: string, contractAddress?: string) {
    const qp = new URLSearchParams();
    if (contractAddress) qp.set('contractAddress', contractAddress);
    return this.request<{ tasks: AgentTaskRecord[] }>(`/agent-tasks?${qp.toString()}`, {}, { bearerToken: token });
  }

  createAgentTask(token: string, input: Record<string, unknown>) {
    return this.request<{ task: AgentTaskRecord }>(
      '/agent-tasks',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      { bearerToken: token }
    );
  }

  listDisputes(token: string, status?: 'open' | 'resolved' | 'dismissed') {
    const qp = new URLSearchParams();
    if (status) qp.set('status', status);
    return this.request<{ disputes: ContractDisputeRecord[] }>(`/disputes?${qp.toString()}`, {}, { bearerToken: token });
  }
}

export default AicfClient;
export * from './contractHelpers.js';
