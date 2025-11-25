/* -----------------------------------------------------------------------------
 * AICF Types (Explorer)
 *
 * Provider directory, Job lifecycle, Settlements and SLA/quality metadata used
 * by the Explorer UI. Shapes are intentionally permissive to interop with
 * multiple backends (node RPC, services API) without tight coupling.
 * -------------------------------------------------------------------------- */

import type { Address, Hex } from './core';

/* -----------------------------------------------------------------------------
 * Providers & Models
 * -------------------------------------------------------------------------- */

export type AICFKind = 'ai' | 'quantum';

export interface Pricing {
  /** Unit this price applies to. */
  unit: 'tok' | 'sec' | 'shot' | 'task';
  /**
   * Decimal string rate per unit (e.g. "0.000002" tokens/sec).
   * Use strings to avoid precision loss.
   */
  rate: string;
  /** Optional currency/denom (e.g. "ANIMI", "uANIMI"). */
  currency?: string;
}

export interface SLATerms {
  /** Rolling window used for compliance checks (days). */
  windowDays: number;
  /** Minimum success ratio expected in window (0..1). */
  minSuccessRate: number;
  /** P95 latency cap for completion (milliseconds). */
  maxP95LatencyMs: number;
  /** Optional time-to-first-byte cap (ms). */
  maxTTFBMs?: number;
  /** Optional economic incentives. Basis points (1/100 of a %). */
  incentives?: {
    rebateBps?: number;   // refund to requester on breach
    penaltyBps?: number;  // slash/penalty to provider on breach
  };
}

export interface ModelInfo {
  /** Unique model/circuit id scoped to the provider (e.g. "gpt4o-mini"). */
  id: string;
  /** Workload kind. */
  kind: AICFKind;
  /** Optional semantic information for the UI. */
  description?: string;
  version?: string;
  capabilities?: string[]; // e.g. ["json", "stream", "function-call"]
  /** Pricing matrix (multiple entries allowed: prompt vs. output, etc). */
  pricing?: Pricing[];
  /** Hard caps advertised by the provider. */
  limits?: {
    maxPromptTokens?: number;
    maxOutputTokens?: number;
    maxShots?: number;
    maxQubits?: number;
    maxRuntimeSec?: number;
  };
  /** SLA terms specific to this model (overrides provider-level defaults). */
  sla?: SLATerms;
}

export interface ProviderQoS {
  /** Availability over recent window (0..1). */
  uptime?: number;
  /** Success ratio over recent window (0..1). */
  successRate?: number;
  /** Latency stats (milliseconds). */
  latencyAvgMs?: number;
  latencyP95Ms?: number;
  /** Unix seconds when this snapshot was computed. */
  lastUpdated?: number;
}

export interface ProviderCapacity {
  /** Maximum concurrent jobs the provider will accept. */
  concurrent?: number;
  /** Queue length presently waiting at the provider. */
  queued?: number;
  /** Available capacity right now (best-effort). */
  available?: number;
}

export interface Provider {
  /** Canonical provider identifier (could be address, DID, or slug). */
  id: string;
  /** Optional settlement/control address on-chain. */
  address?: Address;
  /** Human-friendly label. */
  name?: string;
  /** Base endpoint for job submission. */
  endpoint?: string;
  /** Tags for discovery (e.g. "gpu", "h100", "verifiable"). */
  tags?: string[];

  /** Supported models/circuits. */
  models: ModelInfo[];

  /** Quality snapshot and capacity hints. */
  qos?: ProviderQoS;
  capacity?: ProviderCapacity;

  /** Economic posture (optional). */
  stake?: string;     // decimal string
  stakeDenom?: string;
}

/* Type guards */
export const isAIModel = (m: ModelInfo) => m.kind === 'ai';
export const isQuantumModel = (m: ModelInfo) => m.kind === 'quantum';

/* -----------------------------------------------------------------------------
 * Jobs
 * -------------------------------------------------------------------------- */

export type JobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'expired'
  | 'settled';

export interface JobCostEstimate {
  /** Estimated units (e.g. tokens/seconds/shots). */
  units: number;
  /** Rate and currency for transparency. */
  pricing: Pricing;
  /** Estimated total cost as decimal string. */
  total: string;
}

export interface AIJobInput {
  kind: 'ai';
  modelId: string;
  prompt: string;
  /** Optional raw input payload (e.g. tool schema, images as data URLs). */
  extras?: Record<string, unknown>;
  /** Output token cap or sampling params. */
  params?: {
    maxTokens?: number;
    temperature?: number;
    topP?: number;
    json?: boolean;
  };
}

export interface QuantumJobInput {
  kind: 'quantum';
  modelId: string; // circuit backend id
  /** Circuit description (OpenQASM/JSON IR/etc). */
  circuit: string | Record<string, unknown>;
  /** Number of shots/samples. */
  shots?: number;
  /** Backend-specific extras. */
  params?: Record<string, unknown>;
}

export type JobInput = AIJobInput | QuantumJobInput;

export interface AIJobResult {
  kind: 'ai';
  /** Final text (or JSON if params.json=true). */
  output?: string;
  /** Optional token-level accounting. */
  usage?: {
    promptTokens?: number;
    outputTokens?: number;
    totalTokens?: number;
  };
  /** Provider-supplied raw payload for advanced UIs. */
  raw?: unknown;
}

export interface QuantumJobResult {
  kind: 'quantum';
  /** Measurement histogram: bitstring -> count. */
  counts?: Record<string, number>;
  /** Optional statevector / density matrix summaries. */
  state?: unknown;
  /** Backend-supplied raw payload. */
  raw?: unknown;
}

export type JobResult = AIJobResult | QuantumJobResult;

export interface JobError {
  code: string;
  message: string;
  /** Optional provider-specific detail. */
  detail?: unknown;
}

export interface Job {
  id: string;
  kind: AICFKind;

  /** Provider selection. */
  providerId: string;

  /** Submitted input and user tag for correlation. */
  input: JobInput;
  tag?: string;

  /** Lifecycle */
  status: JobStatus;
  submittedAt: number;   // unix seconds
  startedAt?: number;
  finishedAt?: number;
  expiresAt?: number;

  /** Economics */
  estimate?: JobCostEstimate;
  /** Final cost as decimal string (if billed post-completion). */
  finalCost?: string;
  denom?: string;

  /** Result or error (mutually exclusive). */
  result?: JobResult;
  error?: JobError;

  /** Settlement linkage (tx hash if paid on-chain). */
  settlementTx?: Hex;
}

/* Type guards */
export const isAIJob = (j: Job | JobInput): j is Job & { input: AIJobInput } =>
  (j as Job).input?.kind === 'ai' || (j as AIJobInput).kind === 'ai';

export const isQuantumJob = (
  j: Job | JobInput
): j is Job & { input: QuantumJobInput } =>
  (j as Job).input?.kind === 'quantum' || (j as QuantumJobInput).kind === 'quantum';

/* -----------------------------------------------------------------------------
 * SLA Reporting (observed vs. terms)
 * -------------------------------------------------------------------------- */

export interface SLAObserved {
  windowDays: number;
  successRate?: number;   // 0..1
  uptime?: number;        // 0..1
  latencyAvgMs?: number;
  latencyP95Ms?: number;
  /** Number of samples used to compute this snapshot. */
  samples?: number;
  /** Timestamp when computed (unix seconds). */
  ts?: number;
}

export interface SLACompliance {
  /** True if observed meets or exceeds terms. */
  ok: boolean;
  /** Fields that breached thresholds. */
  breaches: Array<'successRate' | 'latencyP95' | 'uptime' | 'ttfb'>;
}

export interface SLAReport {
  providerId: string;
  modelId?: string;
  terms: SLATerms;
  observed: SLAObserved;
  compliance: SLACompliance;
}

/* -----------------------------------------------------------------------------
 * Settlements
 * -------------------------------------------------------------------------- */

export type SettlementStatus = 'pending' | 'paid' | 'disputed';

export interface Settlement {
  id: string;
  jobId: string;
  providerId: string;

  /** Economic amounts as decimal strings to avoid FP issues. */
  amount: string;
  denom: string;

  /** Address that paid and the provider address that received. */
  payer?: Address;
  payee?: Address;

  /** On-chain linkage (if applicable). */
  txHash?: Hex;
  blockNumber?: number;

  /** SLA reconciliation outcome for this job. */
  sla?: {
    complied: boolean;
    /** Applied credits or penalties (decimal string). */
    adjustment?: string;
    reason?: string; // short label for UI
  };

  status: SettlementStatus;
  createdAt: number; // unix seconds
  settledAt?: number;
}

/* Convenience helpers */
export const settlementPaid = (s: Settlement) => s.status === 'paid';
export const providerActive = (p: Provider) =>
  (p.qos?.uptime ?? 0) > 0.95 && (p.capacity?.available ?? 0) > 0;

