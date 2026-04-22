import type {
  AgentTaskRecord,
  ContractDisputeRecord,
  ContractJobRecord,
  ContractRecord,
  JobRecord,
  ModelDefinition,
  Project,
  ProviderNode,
  ProviderProfile,
  UsageRecord
} from '@animica/aicf-shared';
import type { SessionState } from './session';

class AicfWebApi {
  constructor(private readonly baseUrl: string) {}

  resolveUrl(path: string) {
    return `${this.baseUrl}${path}`;
  }

  private async request<T>(path: string, init: RequestInit = {}, session?: SessionState | null): Promise<T> {
    const headers = new Headers(init.headers ?? {});
    headers.set('Content-Type', 'application/json');

    if (session?.token) {
      headers.set('x-session-token', session.token);
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers
      });
    } catch (error) {
      throw new Error(
        `Unable to reach AICF API at ${this.baseUrl}. Check API availability and VITE_AICF_API_BASE_URL.`
      );
    }

    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload?.error?.message ?? `HTTP ${response.status}`);
    }

    return payload as T;
  }

  signup(input: { email: string; password: string; role: 'developer' | 'provider' }) {
    return this.request<{ token: string; user: SessionState['user'] }>('/auth/signup', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  login(input: { email: string; password: string }) {
    return this.request<{ token: string; user: SessionState['user'] }>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(input)
    });
  }

  getMe(session: SessionState) {
    return this.request<{ user: SessionState['user'] }>('/auth/me', {}, session);
  }

  linkWallet(session: SessionState, input: { address: string; chainId: number; signature: string }) {
    return this.request<{ user: SessionState['user'] }>('/wallet/link', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  governanceConfig(session: SessionState) {
    return this.request<{ config: Record<string, unknown> }>('/wallet/governance-config', {}, session);
  }

  listProjects(session: SessionState) {
    return this.request<{ projects: Project[] }>('/projects', {}, session);
  }

  createProject(session: SessionState, input: { name: string; slug: string; description?: string }) {
    return this.request<{ project: Project }>('/projects', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  updateProject(session: SessionState, projectId: string, input: { description?: string; webhookUrl?: string }) {
    return this.request<{ project: Project }>(`/projects/${projectId}`, {
      method: 'PATCH',
      body: JSON.stringify(input)
    }, session);
  }

  fundProject(session: SessionState, projectId: string, input: { amountAnm: string; txHash?: string }) {
    return this.request<{
      project: Project;
      contractCall: {
        contractAddress: string;
        method: string;
        args: Record<string, unknown>;
      };
    }>(`/projects/${projectId}/fund`, {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  withdrawProject(session: SessionState, projectId: string, amountAnmNanos: string) {
    return this.request<{ project: Project }>(`/projects/${projectId}/withdraw`, {
      method: 'POST',
      body: JSON.stringify({ amountAnmNanos })
    }, session);
  }

  listApiKeys(session: SessionState, projectId: string) {
    return this.request<{ apiKeys: Array<{ id: string; name: string; prefix: string; scopes: string[]; revokedAt?: string }> }>(
      `/projects/${projectId}/api-keys`,
      {},
      session
    );
  }

  createApiKey(session: SessionState, projectId: string, input: { name: string; scopes: string[] }) {
    return this.request<{
      keyId: string;
      prefix: string;
      token: string;
      scopes: string[];
      createdAt: string;
    }>(`/projects/${projectId}/api-keys`, {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  revokeApiKey(session: SessionState, keyId: string) {
    return this.request<{ key: { id: string; revokedAt: string } }>(`/projects/api-keys/${keyId}/revoke`, {
      method: 'POST',
      body: '{}'
    }, session);
  }

  listModels() {
    return this.request<{ object: string; data: ModelDefinition[] }>('/models');
  }

  chatCompletions(
    input: {
      model: string;
      messages: Array<{ role: 'system' | 'user' | 'assistant'; content: string }>;
      max_tokens?: number;
      stream?: boolean;
      metadata?: Record<string, unknown>;
    },
    apiKey: string
  ) {
    return this.request<{
      id: string;
      object: string;
      created: number;
      model: string;
      choices: Array<{ index: number; message: { role: string; content: string }; finish_reason: string }>;
      usage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
      aicf?: Record<string, unknown>;
    }>('/v1/chat/completions', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`
      },
      body: JSON.stringify(input)
    });
  }

  listJobs(session: SessionState, projectId?: string) {
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : '';
    return this.request<{ jobs: JobRecord[] }>(`/jobs${query}`, {}, session);
  }

  createJob(session: SessionState, input: Record<string, unknown>) {
    return this.request<{ job: JobRecord }>('/jobs', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  listUsage(session: SessionState, projectId?: string) {
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : '';
    return this.request<{ usage: UsageRecord[] }>(`/usage${query}`, {}, session);
  }

  listSettlements(session: SessionState, projectId?: string) {
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : '';
    return this.request<{ settlements: Array<Record<string, unknown>> }>(`/usage/settlements${query}`, {}, session);
  }

  registerProvider(session: SessionState, input: { walletAddress: string; signature: string; daemonPublicKey: string }) {
    return this.request<{
      provider: ProviderProfile;
      daemonToken: string;
      contractCalls: Record<string, unknown>;
    }>('/provider/register', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  providerProfile(session: SessionState) {
    return this.request<{ provider: ProviderProfile; rewardBalanceAnmNanos: string }>('/provider/profile', {}, session);
  }

  providerStake(session: SessionState, amountAnmNanos: string) {
    return this.request<{ provider: ProviderProfile }>('/provider/stake', {
      method: 'POST',
      body: JSON.stringify({ amountAnmNanos })
    }, session);
  }

  providerUnstake(session: SessionState, amountAnmNanos: string) {
    return this.request<{ provider: ProviderProfile }>('/provider/unstake', {
      method: 'POST',
      body: JSON.stringify({ amountAnmNanos })
    }, session);
  }

  registerNode(session: SessionState, input: Record<string, unknown>) {
    return this.request<{ node: ProviderNode }>('/provider/nodes', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  listNodes(session: SessionState) {
    return this.request<{ nodes: ProviderNode[] }>('/provider/nodes', {}, session);
  }

  listProviderJobs(session: SessionState) {
    return this.request<{ jobs: JobRecord[] }>('/provider/jobs', {}, session);
  }

  listProviderContractJobs(session: SessionState) {
    return this.request<{ jobs: ContractJobRecord[] }>('/provider/contract-jobs', {}, session);
  }

  claimProviderRewards(session: SessionState) {
    return this.request<{ claimedAnmNanos: string; contractCall: Record<string, unknown> }>('/provider/rewards/claim', {
      method: 'POST',
      body: '{}'
    }, session);
  }

  openDispute(session: SessionState, jobId: string, reason: string) {
    return this.request<{ job: JobRecord }>(`/jobs/${jobId}/disputes`, {
      method: 'POST',
      body: JSON.stringify({ reason })
    }, session);
  }

  adminOverview(session: SessionState) {
    return this.request<Record<string, unknown>>('/admin/overview', {}, session);
  }

  adminProviders(session: SessionState) {
    return this.request<{ providers: Array<Record<string, unknown>> }>('/admin/providers', {}, session);
  }

  adminJobs(session: SessionState) {
    return this.request<{ jobs: JobRecord[] }>('/admin/jobs', {}, session);
  }

  adminContractJobs(session: SessionState, state?: string) {
    const query = state ? `?state=${encodeURIComponent(state)}` : '';
    return this.request<{ jobs: ContractJobRecord[] }>(`/admin/contract-jobs${query}`, {}, session);
  }

  adminDisputes(session: SessionState) {
    return this.request<{ disputes: JobRecord[] }>('/admin/disputes', {}, session);
  }

  adminContractDisputes(session: SessionState, status?: 'open' | 'resolved' | 'dismissed') {
    const query = status ? `?status=${status}` : '';
    return this.request<{ disputes: ContractDisputeRecord[] }>(`/admin/contract-disputes${query}`, {}, session);
  }

  adminResolveDispute(
    session: SessionState,
    jobId: string,
    input: { action: 'uphold_provider' | 'slash_provider'; slashAmountAnmNanos?: string; note?: string }
  ) {
    return this.request<{ job: JobRecord }>(`/admin/disputes/${jobId}/resolve`, {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  adminResolveContractDispute(
    session: SessionState,
    disputeId: string,
    input: { action: 'slash' | 'clear' | 'refund_requester'; slashAmountAnmNanos?: string; note?: string }
  ) {
    return this.request<{ dispute: ContractDisputeRecord; job: ContractJobRecord }>(
      `/admin/contract-disputes/${disputeId}/resolve`,
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }

  adminTreasury(session: SessionState) {
    return this.request<{ treasury: Record<string, unknown>; grants: Array<Record<string, unknown>> }>('/admin/treasury', {}, session);
  }

  adminFinalizationQueue(session: SessionState) {
    return this.request<{ jobs: ContractJobRecord[] }>('/admin/finalization-queue', {}, session);
  }

  adminFinalizeContractJob(session: SessionState, jobId: string) {
    return this.request<{ job: ContractJobRecord; settlement: Record<string, unknown> }>(
      `/admin/contract-jobs/${jobId}/finalize`,
      {
        method: 'POST',
        body: '{}'
      },
      session
    );
  }

  adminGrant(session: SessionState, input: { projectId: string; amountAnmNanos: string; reason: string }) {
    return this.request<Record<string, unknown>>('/admin/treasury/grants', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  adminDepositTreasury(session: SessionState, input: { amountAnmNanos: string; sourceTxHash?: string; note?: string }) {
    return this.request<{
      treasury: Record<string, unknown>;
      contractCall: {
        contractAddress: string;
        method: string;
        args: Record<string, unknown>;
      };
    }>('/admin/treasury/deposit', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  adminModelRouting(session: SessionState) {
    return this.request<{ models: ModelDefinition[] }>('/admin/model-routing', {}, session);
  }

  adminFeatureFlags(session: SessionState) {
    return this.request<{ flags: Array<{ key: string; enabled: boolean; note?: string }> }>('/admin/feature-flags', {}, session);
  }

  adminSetFeatureFlag(session: SessionState, input: { key: string; enabled: boolean; note?: string }) {
    return this.request<{ flag: { key: string; enabled: boolean } }>('/admin/feature-flags', {
      method: 'POST',
      body: JSON.stringify(input)
    }, session);
  }

  adminPause(session: SessionState, paused: boolean, reason?: string) {
    return this.request<{ paused: boolean }>('/admin/pause', {
      method: 'POST',
      body: JSON.stringify({ paused, reason })
    }, session);
  }

  status() {
    return this.request<Record<string, unknown>>('/status');
  }

  listMinerDownloads() {
    return this.request<{
      generatedAt: string;
      releases: Array<{
        filename: string;
        version: string;
        network: string;
        platform: 'linux' | 'macos' | 'windows';
        buildId: string;
        extension: string;
        sizeBytes: number;
        modifiedAt: string;
        downloadPath: string;
      }>;
      latestByPlatform: Partial<
        Record<
          'linux' | 'macos' | 'windows',
          {
            filename: string;
            version: string;
            network: string;
            platform: 'linux' | 'macos' | 'windows';
            buildId: string;
            extension: string;
            sizeBytes: number;
            modifiedAt: string;
            downloadPath: string;
          }
        >
      >;
    }>('/downloads/miners');
  }

  listProviderDownloads() {
    return this.request<{
      generatedAt: string;
      manifest: {
        source?: string;
        version?: string;
        generated_at?: string;
        checksum_file?: string;
        items: Array<{
          platform: 'windows' | 'linux' | 'python';
          label: string;
          filename: string;
          version: string;
          size_bytes: number;
          sha256: string;
          url: string;
          release_notes?: string;
          notes?: string;
        }>;
      };
    }>('/downloads/providers');
  }

  listContracts(session: SessionState) {
    return this.request<{ contracts: ContractRecord[] }>('/contracts', {}, session);
  }

  registerContract(
    session: SessionState,
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
      session
    );
  }

  upsertContractArtifacts(
    session: SessionState,
    address: string,
    input: {
      sourceCode?: string;
      sourceLanguage?: string;
      abiJson?: string;
    }
  ) {
    return this.request<{
      contract: ContractRecord;
      artifacts: Array<{
        id: string;
        contractAddress: string;
        kind: 'source' | 'abi';
        language?: string;
        content: string;
        createdBy: string;
        createdAt: string;
      }>;
    }>(
      `/contracts/${address}/artifacts`,
      {
        method: 'PUT',
        body: JSON.stringify(input)
      },
      session
    );
  }

  getContract(session: SessionState, address: string) {
    return this.request<{ contract: ContractRecord }>(`/contracts/${address}`, {}, session);
  }

  setContractPaused(session: SessionState, address: string, paused: boolean) {
    return this.request<{ contract: ContractRecord }>(
      `/contracts/${address}/pause`,
      {
        method: 'POST',
        body: JSON.stringify({ paused })
      },
      session
    );
  }

  listContractJobs(
    session: SessionState,
    filter?: { contractAddress?: string; state?: string; modelId?: string; providerId?: string; disputeStatus?: string }
  ) {
    const params = new URLSearchParams();
    if (filter?.contractAddress) params.set('contractAddress', filter.contractAddress);
    if (filter?.state) params.set('state', filter.state);
    if (filter?.modelId) params.set('modelId', filter.modelId);
    if (filter?.providerId) params.set('providerId', filter.providerId);
    if (filter?.disputeStatus) params.set('disputeStatus', filter.disputeStatus);
    return this.request<{ jobs: ContractJobRecord[] }>(
      `/contract-jobs${params.size > 0 ? `?${params.toString()}` : ''}`,
      {},
      session
    );
  }

  createContractJob(session: SessionState, input: Record<string, unknown>) {
    return this.request<{
      job: ContractJobRecord;
      escrowEvents: Array<Record<string, unknown>>;
      contractCalls: Record<string, unknown>;
    }>(
      '/contract-jobs',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }

  getContractJob(session: SessionState, jobId: string) {
    return this.request<{
      job: ContractJobRecord;
      commitments: Array<Record<string, unknown>>;
      assignments: Array<Record<string, unknown>>;
      escrowEvents: Array<Record<string, unknown>>;
    }>(`/contract-jobs/${jobId}`, {}, session);
  }

  acceptContractJob(session: SessionState, jobId: string, acceptedHash?: string) {
    return this.request<{ job: ContractJobRecord }>(
      `/contract-jobs/${jobId}/accept`,
      {
        method: 'POST',
        body: JSON.stringify({ acceptedHash })
      },
      session
    );
  }

  challengeContractJob(session: SessionState, jobId: string, reasonCode: string, evidenceRef?: string) {
    return this.request<{ job: ContractJobRecord; dispute: ContractDisputeRecord }>(
      `/contract-jobs/${jobId}/disputes`,
      {
        method: 'POST',
        body: JSON.stringify({ reasonCode, evidenceRef })
      },
      session
    );
  }

  finalizeContractJob(session: SessionState, jobId: string) {
    return this.request<{ job: ContractJobRecord; settlement: Record<string, unknown> }>(
      `/contract-jobs/${jobId}/finalize`,
      {
        method: 'POST',
        body: '{}'
      },
      session
    );
  }

  refundContractJobIfExpired(session: SessionState, jobId: string) {
    return this.request<{ job: ContractJobRecord }>(
      `/contract-jobs/${jobId}/refund-if-expired`,
      {
        method: 'POST',
        body: '{}'
      },
      session
    );
  }

  listDisputes(session: SessionState, status?: 'open' | 'resolved' | 'dismissed') {
    const query = status ? `?status=${status}` : '';
    return this.request<{ disputes: ContractDisputeRecord[] }>(`/disputes${query}`, {}, session);
  }

  resolveDispute(
    session: SessionState,
    disputeId: string,
    input: { action: 'slash' | 'clear' | 'refund_requester'; slashAmountAnmNanos?: string; note?: string }
  ) {
    return this.request<{ dispute: ContractDisputeRecord; job: ContractJobRecord }>(
      `/disputes/${disputeId}/resolve`,
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }

  listAgentTasks(session: SessionState, contractAddress?: string) {
    const query = contractAddress ? `?contractAddress=${encodeURIComponent(contractAddress)}` : '';
    return this.request<{ tasks: AgentTaskRecord[] }>(`/agent-tasks${query}`, {}, session);
  }

  createAgentTask(session: SessionState, input: Record<string, unknown>) {
    return this.request<{ task: AgentTaskRecord }>(
      '/agent-tasks',
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }

  getAgentTask(session: SessionState, taskId: string) {
    return this.request<{ task: AgentTaskRecord }>(`/agent-tasks/${taskId}`, {}, session);
  }

  appendAgentTaskStep(session: SessionState, taskId: string, input: { commitmentHash: string; traceRef?: string }) {
    return this.request<{ task: AgentTaskRecord }>(
      `/agent-tasks/${taskId}/steps`,
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }

  submitAgentTaskFinalResult(session: SessionState, taskId: string, input: { resultHash: string; resultRef: string }) {
    return this.request<{ task: AgentTaskRecord }>(
      `/agent-tasks/${taskId}/final-result`,
      {
        method: 'POST',
        body: JSON.stringify(input)
      },
      session
    );
  }
}

function normalizeBaseUrl(value: string) {
  return value.endsWith('/') ? value.slice(0, -1) : value;
}

function resolveApiBaseUrl() {
  const configured = import.meta.env.VITE_AICF_API_BASE_URL?.trim();
  if (configured) return normalizeBaseUrl(configured);

  // Default to same-origin `/api` so hosted HTTPS deployments avoid localhost/mixed-content failures.
  return '/api';
}

const apiBaseUrl = resolveApiBaseUrl();
export const aicfApi = new AicfWebApi(apiBaseUrl);
