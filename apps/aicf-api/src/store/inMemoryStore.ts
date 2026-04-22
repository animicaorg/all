import type {
  AccountUser,
  AgentTaskRecord,
  ApiKeyRecord,
  AuditLogEntry,
  ContractDisputeRecord,
  ContractJobChainEvent,
  ContractJobRecord,
  ContractArtifactRecord,
  ContractRecord,
  FeatureFlag,
  GrantAllocation,
  JobRecord,
  JobAssignmentRecord,
  ModelDefinition,
  Project,
  ProviderNode,
  ProviderProfile,
  ResultCommitmentRecord,
  EscrowEventRecord,
  SettlementRecord,
  TreasurySnapshot,
  UsageRecord
} from '@animica/aicf-shared';

export type InMemoryStore = {
  users: Map<string, AccountUser>;
  usersByEmail: Map<string, string>;
  sessions: Map<string, { userId: string; createdAt: string }>;
  projects: Map<string, Project>;
  apiKeys: Map<string, ApiKeyRecord>;
  apiKeysByPrefix: Map<string, string>;
  providers: Map<string, ProviderProfile>;
  providerNodes: Map<string, ProviderNode>;
  providerNodesByProviderId: Map<string, Set<string>>;
  jobs: Map<string, JobRecord>;
  usage: Map<string, UsageRecord>;
  settlements: Map<string, SettlementRecord>;
  treasurySnapshots: Map<string, TreasurySnapshot>;
  grants: Map<string, GrantAllocation>;
  featureFlags: Map<string, FeatureFlag>;
  auditLogs: Map<string, AuditLogEntry>;
  models: Map<string, ModelDefinition>;
  providerJobQueue: Map<string, string[]>;
  rewardLedger: Map<string, bigint>;
  paused: boolean;
  contracts: Map<string, ContractRecord>;
  contractsByAddress: Map<string, string>;
  contractArtifacts: Map<string, ContractArtifactRecord>;
  contractJobs: Map<string, ContractJobRecord>;
  contractJobsByOnchainId: Map<string, string>;
  agentTasks: Map<string, AgentTaskRecord>;
  agentTasksByOnchainId: Map<string, string>;
  jobAssignments: Map<string, JobAssignmentRecord>;
  resultCommitments: Map<string, ResultCommitmentRecord>;
  contractDisputes: Map<string, ContractDisputeRecord>;
  escrowEvents: Map<string, EscrowEventRecord>;
  chainEvents: Map<string, ContractJobChainEvent>;
  chainEventQueue: string[];
  contractProviderQueue: Map<string, string[]>;
};

export function createInMemoryStore(): InMemoryStore {
  return {
    users: new Map(),
    usersByEmail: new Map(),
    sessions: new Map(),
    projects: new Map(),
    apiKeys: new Map(),
    apiKeysByPrefix: new Map(),
    providers: new Map(),
    providerNodes: new Map(),
    providerNodesByProviderId: new Map(),
    jobs: new Map(),
    usage: new Map(),
    settlements: new Map(),
    treasurySnapshots: new Map(),
    grants: new Map(),
    featureFlags: new Map(),
    auditLogs: new Map(),
    models: new Map(),
    providerJobQueue: new Map(),
    rewardLedger: new Map(),
    paused: false,
    contracts: new Map(),
    contractsByAddress: new Map(),
    contractArtifacts: new Map(),
    contractJobs: new Map(),
    contractJobsByOnchainId: new Map(),
    agentTasks: new Map(),
    agentTasksByOnchainId: new Map(),
    jobAssignments: new Map(),
    resultCommitments: new Map(),
    contractDisputes: new Map(),
    escrowEvents: new Map(),
    chainEvents: new Map(),
    chainEventQueue: [],
    contractProviderQueue: new Map()
  };
}
