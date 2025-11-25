/**
 * Shared app types for Studio Web.
 *
 * These types intentionally avoid importing heavy runtime deps.
 * For chain primitives (Tx/Receipt/Block/Head), prefer importing from @animica/sdk
 * in feature modules. We mirror a few shapes here for convenience and app-specific
 * state wiring.
 */

/* ──────────────────────────────────────────────────────────────────────────── */
/* Basic primitives                                                             */
/* ──────────────────────────────────────────────────────────────────────────── */

export type Hex = `0x${string}`;
export type Base64 = string;

/** Bech32m address string: "anim1..." */
export type Address = string;

/** ChainId is a small integer; studio treats it as number. */
export type ChainId = number;

/* ──────────────────────────────────────────────────────────────────────────── */
/* Project files & templates                                                    */
/* ──────────────────────────────────────────────────────────────────────────── */

export type ProjectFileKind = 'source' | 'manifest' | 'abi' | 'ir' | 'other';
export type ProjectLanguage = 'python' | 'json' | 'ir' | 'text';

export interface ProjectFile {
  /** Path in the virtual project (e.g. "contracts/counter/contract.py") */
  path: string;
  /** Basename derived from path (cached for convenience) */
  name: string;
  kind: ProjectFileKind;
  language: ProjectLanguage;
  content: string;
  readonly?: boolean;
  dirty?: boolean;
}

export interface ProjectState {
  files: ProjectFile[];
  activePath?: string;
}

export interface TemplateFileMeta {
  path: string;
  kind: Extract<ProjectFileKind, 'source' | 'manifest' | 'abi'>;
}

export interface TemplateMeta {
  id: string;
  name: string;
  description?: string;
  files: TemplateFileMeta[];
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* ABI & manifests (trimmed, app-facing)                                       */
/* ──────────────────────────────────────────────────────────────────────────── */

export interface AbiParam {
  name: string;
  type: 'int' | 'bool' | 'bytes' | 'address' | 'string' | string; // allow forwards compat
}

export interface AbiFunction {
  name: string;
  inputs: AbiParam[];
  outputs?: AbiParam[];
  /** Pure/view calls are simulated only. */
  view?: boolean;
  /** Payable calls may spend value/fees; surfaced in UI. */
  payable?: boolean;
}

export interface AbiEvent {
  name: string;
  /** Map of arg name → type */
  args: Record<string, AbiParam['type']>;
  /** Optional topic selector if defined by the toolchain. */
  topic?: Hex;
}

export interface ContractAbi {
  functions: AbiFunction[];
  events?: AbiEvent[];
  errors?: Record<string, string>;
}

/** Minimal manifest shape the IDE cares about. */
export interface ContractManifest {
  name: string;
  version?: string;
  abi: ContractAbi;
  /** Additional metadata shown in UI or passed to services. */
  metadata?: Record<string, unknown>;
  /** Optional resources/caps placeholders per spec. */
  resources?: Record<string, unknown>;
  caps?: Record<string, unknown>;
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Chain core (light mirrors for UI)                                           */
/* Prefer importing exact types from @animica/sdk in data-facing modules.      */
/* ──────────────────────────────────────────────────────────────────────────── */

export interface Head {
  number: number;
  hash: Hex;
  parentHash: Hex;
  timestamp: number; // seconds
}

export interface LogEvent {
  address: Address;
  topics: Hex[];
  data: Hex;
}

export interface Receipt {
  status: 'SUCCESS' | 'REVERT' | 'OOG' | 'FAIL' | string;
  gasUsed: number;
  logs: LogEvent[];
  transactionHash: Hex;
  blockHash?: Hex;
  blockNumber?: number;
}

export interface Tx {
  hash: Hex;
  from: Address;
  to?: Address | null;
  nonce: number;
  gasLimit: number;
  gasPrice: bigint | string | number;
  value?: bigint | string | number;
  data?: Hex;
  chainId: ChainId;
}

export interface Block {
  hash: Hex;
  number: number;
  parentHash: Hex;
  timestamp: number;
  txs: Tx[];
  receiptsRoot?: Hex;
  logsBloom?: Hex;
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Deploy & verify flows                                                        */
/* ──────────────────────────────────────────────────────────────────────────── */

export type DeployStatus =
  | 'idle'
  | 'building'
  | 'signing'
  | 'sending'
  | 'pending'
  | 'confirmed'
  | 'failed';

export interface DeployProgress {
  status: DeployStatus;
  txHash?: Hex;
  address?: Address;
  receipt?: Receipt;
  error?: string;
}

export type VerifyStatus = 'pending' | 'matched' | 'mismatch' | 'error';

export interface VerifyJob {
  id: string;
  address: Address;
  status: VerifyStatus;
  codeHash?: Hex;
  createdAt: string;
  updatedAt?: string;
  errors?: string[];
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Artifacts (via studio-services)                                              */
/* ──────────────────────────────────────────────────────────────────────────── */

export interface Artifact {
  id: string;
  /** Code/content hash (hex). */
  codeHash: Hex;
  /** Size in bytes of the primary artifact blob. */
  size: number;
  /** MIME type (e.g., "application/json"). */
  mime?: string;
  /** ISO timestamp when stored. */
  createdAt: string;
  /** Optional link to chain address the artifact refers to. */
  address?: Address;
  /** Optional ABI/manifest (denormalized for convenience). */
  abi?: ContractAbi;
  manifest?: ContractManifest;
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Data Availability (DA)                                                       */
/* ──────────────────────────────────────────────────────────────────────────── */

export interface BlobCommitment {
  namespace: number;
  commitment: Hex; // commitment root
  nmtRoot: Hex; // namespaced merkle tree root (DA root)
  size: number;
}

export interface BlobReceipt extends BlobCommitment {
  createdAt: string;
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* AICF (AI / Quantum jobs)                                                     */
/* ──────────────────────────────────────────────────────────────────────────── */

export type JobKind = 'AI' | 'Quantum';

export type JobStatus =
  | 'Queued'
  | 'Assigned'
  | 'Completed'
  | 'Failed'
  | 'Expired';

export interface AICFJob {
  id: string;
  kind: JobKind;
  /** Requester address (optional for UI display). */
  requester?: Address;
  /** For AI jobs. */
  model?: string;
  prompt?: string;
  /** For Quantum jobs. */
  circuit?: unknown;
  shots?: number;

  /** Economic hints (display only). */
  fee?: string | number;
  units?: number;

  status: JobStatus;
  providerId?: string;
  createdAt: string;
  updatedAt?: string;

  /** Deterministic task identifier derived by capabilities/jobs/id. */
  taskId?: string;
  /** Digest of result payload (if completed). */
  resultDigest?: Hex;
}

export interface AICFResult {
  taskId: string;
  /** Content-address hash of the result. */
  digest: Hex;
  size: number;
  mime?: string;
  /** Either a direct URL (from services) or inline base64 for small payloads. */
  url?: string;
  dataBase64?: Base64;
  /** Optional parsed JSON view if MIME is JSON. */
  json?: unknown;
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Randomness beacon                                                            */
/* ──────────────────────────────────────────────────────────────────────────── */

export interface BeaconVDF {
  input: Hex;
  output: Hex;
  proof: Hex; // format per randomness/specs/VDF.md
}

export interface BeaconRound {
  roundId: number;
  openedAt: string; // ISO
  closedAt: string; // ISO
  aggregate?: Hex; // aggregate of reveals
  vdf?: BeaconVDF;
  beacon?: Hex; // final mixed beacon output
}

/* ──────────────────────────────────────────────────────────────────────────── */
/* Utility guards                                                               */
/* ──────────────────────────────────────────────────────────────────────────── */

export function isHex(s: unknown): s is Hex {
  return typeof s === 'string' && /^0x[0-9a-fA-F]*$/.test(s);
}

export function isAddress(s: unknown): s is Address {
  return typeof s === 'string' && s.startsWith('anim1') && s.length >= 10;
}
