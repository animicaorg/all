/**
 * @animica/agent-sdk
 *
 * Re-exports of the core types and high-level helpers so the agent can be
 * embedded inside other apps (web dashboards, IDE plugins, exchange UIs)
 * without taking on the CLI runtime.
 *
 * Stay typed; do not introduce new state here. All persistence is owned by
 * the consumer.
 */

export {
  // Core surfaces
  AgentError,
  ConfigError,
  PatchError,
  RpcError,
} from "@animica/agent-core";

export type {
  AgentConfig,
  AgentMessage,
  AgentProvider,
  AppliedPatch,
  AuditEntry,
  CompletionOptions,
  CompletionResult,
  EstimateInput,
  FileOp,
  Hunk,
  MinerEligibility,
  MinerIdentity,
  MinerLiveStatus,
  MinerSnapshot,
  NodeStatus,
  Patch,
  PatchJournalEntry,
  PricingTable,
  Receipt,
  ReceiptRequest,
  Reward,
  RpcRequest,
  RpcResponse,
  Signer,
  Submission,
  UsageRecord,
  WalletBalance,
  WalletIdentity,
} from "@animica/agent-core";

export {
  // Helpers
  applyPatch,
  BillingEngine,
  createLogger,
  createPatch,
  DEFAULT_CONFIG,
  DEFAULT_PRICING,
  detectMinerIdentity,
  estimate,
  evaluateEligibility,
  fetchBalance,
  formatANM,
  getConfigPaths,
  hashArtifact,
  HttpCoordinator,
  isLikelyAnimicaAddress,
  listJournal,
  loadConfig,
  LocalCoordinator,
  NodeSettlement,
  NoopSigner,
  OfflineProvider,
  OfflineSettlement,
  parseANM,
  pickProvider,
  planResources,
  probeMinerLive,
  probeNode,
  readLatestJournal,
  renderPatchPreview,
  Repo,
  resolveMinerMode,
  resolveWalletIdentity,
  rollbackPatch,
  safeParse,
  safeStringify,
  SessionStore,
  toBigInt,
  UsageJournal,
} from "@animica/agent-core";
