export type UserRole = 'developer' | 'provider' | 'admin';

export type JobClass =
  | 'chat_inference'
  | 'embedding_generation'
  | 'batch_inference'
  | 'fine_tuning_training'
  | 'evaluation'
  | 'retrieval_indexing'
  | 'agent_task'
  | 'custom_compute';

export type JobStatus =
  | 'queued'
  | 'assigned'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'disputed';

export type ProviderState = 'active' | 'inactive' | 'quarantined';

export type SettlementStatus = 'pending' | 'finalized' | 'queued_onchain' | 'reverted';

export type WalletBinding = {
  address: string;
  chainId: number;
  linkedAt: string;
  signature: string;
};

export type AuditLogEntry = {
  id: string;
  actorId: string;
  actorRole: UserRole | 'system';
  action: string;
  resourceType: string;
  resourceId: string;
  createdAt: string;
  ip?: string;
  metadata?: Record<string, unknown>;
};

export type AccountUser = {
  id: string;
  email: string;
  passwordHash: string;
  role: UserRole;
  createdAt: string;
  oauthProvider?: string;
  wallet?: WalletBinding;
};

export type ProjectBalance = {
  availableAnm: string;
  reservedAnm: string;
  totalDepositedAnm: string;
  totalSpentAnm: string;
  totalRefundedAnm: string;
};

export type Project = {
  id: string;
  ownerUserId: string;
  name: string;
  slug: string;
  description: string;
  webhookUrl?: string;
  createdAt: string;
  updatedAt: string;
  balance: ProjectBalance;
};

export type ApiKeyScope =
  | 'inference:chat'
  | 'inference:embeddings'
  | 'jobs:write'
  | 'jobs:read'
  | 'projects:read'
  | 'projects:write';

export type ApiKeyRecord = {
  id: string;
  projectId: string;
  name: string;
  prefix: string;
  hash: string;
  scopes: ApiKeyScope[];
  createdAt: string;
  revokedAt?: string;
  lastUsedAt?: string;
};

export type ProviderNodeCapability = {
  runtime: 'llm' | 'embedding' | 'training' | 'agent' | 'custom';
  gpus: number;
  gpuMemoryGb: number;
  cpus: number;
  ramGb: number;
  region: string;
  modelFamilies: string[];
};

export type ProviderProfile = {
  id: string;
  userId: string;
  walletAddress: string;
  daemonTokenHash: string;
  state: ProviderState;
  reputation: number;
  totalJobsCompleted: number;
  totalJobsFailed: number;
  stakeAnm: string;
  slashHistory: Array<{ id: string; amountAnm: string; reason: string; createdAt: string }>;
  createdAt: string;
};

export type ProviderNode = {
  id: string;
  providerId: string;
  metadata: {
    name: string;
    machineType: string;
    os: string;
    labels: string[];
  };
  capabilities: ProviderNodeCapability;
  benchmark: {
    llmTokensPerSecond: number;
    embeddingVectorsPerSecond: number;
    trainingSamplesPerSecond: number;
    score: number;
  };
  state: ProviderState;
  lastHeartbeatAt: string;
  currentLoad: number;
  queueDepth: number;
};

export type ModelPricing = {
  model: string;
  inputTokenAnmNanos: string;
  outputTokenAnmNanos: string;
  embeddingVectorAnmNanos: string;
  requestBaseAnmNanos: string;
  providerShareBps: number;
  treasuryShareBps: number;
};

export type ModelDefinition = {
  id: string;
  name: string;
  version: string;
  type: 'chat' | 'embedding' | 'training' | 'agent';
  status: 'active' | 'paused' | 'deprecated';
  defaultProviderRouting: 'first_party' | 'provider_network';
  runtimeRequirements: {
    minGpuMemoryGb: number;
    minCpu: number;
    minRamGb: number;
    runtimes: string[];
  };
  pricing: ModelPricing;
};

export type JobBudget = {
  maxAnmNanos: string;
  reservedAnmNanos: string;
  subsidyAnmNanos: string;
};

export type JobRequestPayload = {
  class: JobClass;
  model: string;
  input: Record<string, unknown>;
  timeoutSeconds: number;
  replication: number;
  verificationMode: 'none' | 'sampled' | 'full';
  outputMode: 'private' | 'public';
  callbackUrl?: string;
  challengeWindowSeconds: number;
  regionPreference?: string;
  requiredHardware?: Partial<{
    minGpuMemoryGb: number;
    minCpu: number;
    minRamGb: number;
  }>;
};

export type JobRecord = {
  id: string;
  projectId: string;
  apiKeyId?: string;
  status: JobStatus;
  attempts: number;
  maxAttempts: number;
  request: JobRequestPayload;
  createdAt: string;
  updatedAt: string;
  assignedProviderId?: string;
  assignedNodeId?: string;
  startedAt?: string;
  completedAt?: string;
  failedAt?: string;
  output?: Record<string, unknown>;
  error?: string;
  usage: {
    inputTokens: number;
    outputTokens: number;
    embeddingVectors: number;
    latencyMs: number;
    bytesIn: number;
    bytesOut: number;
  };
  budget: JobBudget;
  settlement: {
    status: SettlementStatus;
    providerRewardAnmNanos: string;
    treasuryCutAnmNanos: string;
    chargedAnmNanos: string;
    refundedAnmNanos: string;
    escrowId?: string;
    settlementId?: string;
  };
};

export type UsageRecord = {
  id: string;
  projectId: string;
  apiKeyId?: string;
  jobId?: string;
  model: string;
  class: JobClass;
  providerId?: string;
  status: 'success' | 'error';
  inputTokens: number;
  outputTokens: number;
  embeddingVectors: number;
  latencyMs: number;
  bytesIn: number;
  bytesOut: number;
  chargedAnmNanos: string;
  providerRewardAnmNanos: string;
  treasuryCutAnmNanos: string;
  subsidyAnmNanos: string;
  createdAt: string;
};

export type TreasurySnapshot = {
  id: string;
  availableAnmNanos: string;
  allocatedSubsidyAnmNanos: string;
  paidProviderAnmNanos: string;
  protocolFeesAnmNanos: string;
  grantsOutstandingAnmNanos: string;
  createdAt: string;
};

export type GrantAllocation = {
  id: string;
  projectId: string;
  amountAnmNanos: string;
  consumedAnmNanos: string;
  reason: string;
  createdAt: string;
  expiresAt?: string;
};

export type SettlementRecord = {
  id: string;
  jobId: string;
  providerId: string;
  projectId: string;
  chargeAnmNanos: string;
  providerRewardAnmNanos: string;
  treasuryCutAnmNanos: string;
  subsidyAnmNanos: string;
  onchainTxHash?: string;
  status: SettlementStatus;
  createdAt: string;
};

export type FeatureFlag = {
  key: string;
  enabled: boolean;
  note?: string;
};

export type AicfConfigSnapshot = {
  chainId: number;
  treasuryAddress: string;
  governanceAddress: string;
  projectBalanceAddress: string;
  jobEscrowAddress: string;
  rewardsAddress: string;
  providerRegistryAddress: string;
  stakeManagerAddress: string;
  disputeManagerAddress: string;
  minProviderStakeAnmNanos: string;
  challengeWindowSeconds: number;
  paused: boolean;
};

export type ContractVerificationMode =
  | 'SINGLE_PROVIDER'
  | 'QUORUM_MATCH'
  | 'VERIFIER_REVIEW'
  | 'CALLBACK_ACCEPT';

export type ContractCallbackMode = 'none' | 'requester_accept' | 'contract_callback';

export type ContractProviderPolicy = {
  mode: 'open' | 'allowlist' | 'blocklist';
  providerIds: string[];
};

export type ContractOutputType =
  | 'text'
  | 'json'
  | 'embeddings_artifact'
  | 'classification_labels'
  | 'tool_trace_bundle'
  | 'agent_transcript_bundle';

export type ContractJobState =
  | 'created'
  | 'funded'
  | 'reserved'
  | 'requested'
  | 'assigned'
  | 'running'
  | 'commitment_submitted'
  | 'result_submitted'
  | 'accepted'
  | 'challenged'
  | 'finalized_paid'
  | 'finalized_refunded'
  | 'cancelled'
  | 'expired';

export type ContractType = 'model_call' | 'agent_task' | 'ai_escrow' | 'custom';

export type ContractRecord = {
  id: string;
  address: string;
  ownerUserId: string;
  type: ContractType;
  metadata: {
    name: string;
    description?: string;
    tags?: string[];
    abiRef?: string;
    sourceRef?: string;
  };
  paused: boolean;
  createdAt: string;
  updatedAt: string;
};

export type ContractArtifactKind = 'source' | 'abi';

export type ContractArtifactRecord = {
  id: string;
  contractAddress: string;
  kind: ContractArtifactKind;
  language?: string;
  content: string;
  createdBy: string;
  createdAt: string;
};

export type ContractJobType = 'model_call' | 'agent_task' | 'embedding' | 'classification' | 'custom';

export type ContractJobRequest = {
  contractAddress: string;
  requester: string;
  payer: string;
  modelId: string;
  jobType: ContractJobType;
  inputRefHash: string;
  outputSchemaRef?: string;
  maxBudgetAnmNanos: string;
  timeoutSeconds: number;
  replication: number;
  quorum: number;
  verificationMode: ContractVerificationMode;
  challengeWindowSeconds: number;
  providerPolicy: ContractProviderPolicy;
  privacy: 'public' | 'private';
  callbackMode: ContractCallbackMode;
  resultType: ContractOutputType;
  metadata?: Record<string, unknown>;
};

export type ContractJobRecord = {
  id: string;
  onchainJobId: string;
  contractAddress: string;
  requester: string;
  payer: string;
  modelId: string;
  jobType: ContractJobType;
  mode: ContractVerificationMode;
  callbackMode: ContractCallbackMode;
  resultType: ContractOutputType;
  providerPolicy: ContractProviderPolicy;
  privacy: 'public' | 'private';
  replication: number;
  quorum: number;
  inputRefHash: string;
  outputSchemaRef?: string;
  state: ContractJobState;
  budgetAnmNanos: string;
  fundedAnmNanos: string;
  reservedAnmNanos: string;
  paidAnmNanos: string;
  refundedAnmNanos: string;
  timeoutAt: string;
  challengeWindowEndsAt?: string;
  assignedProviderIds: string[];
  acceptedResultHash?: string;
  finalResultRef?: string;
  disputeStatus: 'none' | 'open' | 'resolved';
  metadata?: Record<string, unknown>;
  createdAt: string;
  updatedAt: string;
};

export type AgentTaskState =
  | 'created'
  | 'funded'
  | 'running'
  | 'final_result_submitted'
  | 'challenged'
  | 'finalized_paid'
  | 'finalized_refunded'
  | 'cancelled';

export type AgentTaskRecord = {
  id: string;
  onchainTaskId: string;
  contractAddress: string;
  requester: string;
  payer: string;
  modelId: string;
  state: AgentTaskState;
  budgetAnmNanos: string;
  spentAnmNanos: string;
  remainingAnmNanos: string;
  stepCount: number;
  currentStep: number;
  finalResultHash?: string;
  finalResultRef?: string;
  disputeStatus: 'none' | 'open' | 'resolved';
  createdAt: string;
  updatedAt: string;
};

export type JobAssignmentRecord = {
  id: string;
  jobId: string;
  providerId: string;
  nodeId?: string;
  assignedAt: string;
  completedAt?: string;
  status: 'assigned' | 'claimed' | 'completed' | 'failed';
};

export type ResultCommitmentRecord = {
  id: string;
  jobId: string;
  providerId: string;
  resultHash: string;
  resultRef?: string;
  providerSignature: string;
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
  createdAt: string;
};

export type ContractDisputeRecord = {
  id: string;
  jobId: string;
  openedBy: string;
  reasonCode: string;
  evidenceRef?: string;
  status: 'open' | 'resolved' | 'dismissed';
  resolution?: {
    action: 'slash' | 'clear' | 'refund_requester';
    slashAmountAnmNanos?: string;
    note?: string;
    resolvedBy: string;
    resolvedAt: string;
  };
  createdAt: string;
  updatedAt: string;
};

export type EscrowEventType = 'funded' | 'reserved' | 'paid' | 'refunded' | 'slashed' | 'cancelled';

export type EscrowEventRecord = {
  id: string;
  jobId: string;
  type: EscrowEventType;
  amountAnmNanos: string;
  txHash?: string;
  actor: string;
  metadata?: Record<string, unknown>;
  createdAt: string;
};

export type ContractJobChainEvent = {
  id: string;
  eventType:
    | 'MODEL_CALL_REQUESTED'
    | 'MODEL_CALL_FUNDED'
    | 'AGENT_TASK_CREATED'
    | 'AGENT_TASK_STEP_COMMITMENT'
    | 'RESULT_COMMITMENT_SUBMITTED'
    | 'RESULT_REFERENCE_SUBMITTED'
    | 'DISPUTE_OPENED'
    | 'FINALIZED';
  contractAddress: string;
  onchainJobId?: string;
  onchainTaskId?: string;
  payload: Record<string, unknown>;
  observedAt: string;
};
