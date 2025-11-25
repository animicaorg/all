/**
 * types/index.ts
 * ---------------
 * Shared TypeScript types for the studio-wasm package:
 * - Minimal ABI model used by the browser simulator/compiler
 * - Result envelopes for compile / simulate / estimateGas
 * - Small utility aliases (Hex, ByteLike)
 *
 * These types intentionally mirror a subset of the repo-wide ABI schema
 * (spec/abi.schema.json) and the SDK shapes, but are kept lean to avoid
 * heavy cross-package coupling.
 */

/* -------------------------------- Utilities -------------------------------- */

export type Hex = `0x${string}`;

/**
 * Byte-like inputs accepted by helpers. If a string is passed, it may be:
 *  - hex-prefixed (0x…) OR
 *  - UTF-8 text (caller is responsible for intent).
 */
export type ByteLike =
  | Uint8Array
  | ArrayBuffer
  | ArrayBufferView
  | number[]
  | string;

/* ----------------------------------- ABI ----------------------------------- */

/** String form names we accept for ABI scalar types. */
export type AbiTypeName = "int" | "bool" | "bytes" | "address" | "string";

/**
 * Structured form; allows future extension (e.g., sized bytes).
 * For now, only the `kind` field is interpreted by the simulator.
 */
export type AbiType =
  | AbiTypeName
  | {
      kind: AbiTypeName;
      /** For future use (e.g., bits for int, size for bytes) */
      [k: string]: unknown;
    };

export interface AbiParam {
  name: string;
  type: AbiType;
  /** For events: when true, encoded into a topic. Ignored for functions. */
  indexed?: boolean;
}

export interface AbiFunction {
  name: string;
  /** Function selector; if omitted, derived from name+inputs by tooling. */
  selector?: Hex;
  /** True if the call may mutate state; simulation honors this flag logically. */
  mutates?: boolean;
  inputs: AbiParam[];
  outputs?: AbiParam[];
  /** Optional docstring/help text. */
  notice?: string;
}

export interface AbiEvent {
  name: string;
  inputs: AbiParam[];
  /** If true, event has no implicit topic for the name. */
  anonymous?: boolean;
  notice?: string;
}

export interface AbiError {
  name: string;
  inputs?: AbiParam[];
  notice?: string;
}

export interface Abi {
  functions: AbiFunction[];
  events?: AbiEvent[];
  errors?: AbiError[];
}

/* ----------------------------- Contract Manifest --------------------------- */

/**
 * Minimal manifest used by the browser compiler/simulator.
 * In the studio-wasm flow, the "code" is IR bytes produced client-side.
 */
export interface ContractManifest {
  name: string;
  version?: string;
  abi: Abi;
  /**
   * Optional precomputed content hash for integrity. When available, the
   * compiler/simulator will surface it in results for UX consistency.
   */
  codeHash?: Hex;
  /** Free-form metadata (tool can surface in the IDE). */
  metadata?: Record<string, unknown>;
}

/* -------------------------------- Diagnostics ------------------------------- */

export type DiagnosticLevel = "error" | "warning" | "info";

export interface SourceSpan {
  /** 0-based line and column (inclusive) */
  line: number;
  column: number;
  /** 0-based end (exclusive). If omitted, points to start only. */
  endLine?: number;
  endColumn?: number;
}

export interface Diagnostic {
  level: DiagnosticLevel;
  message: string;
  /** Optional pointer into the active source buffer. */
  span?: SourceSpan;
  /** Optional machine-friendly code. */
  code?: string;
  /** Arbitrary data for tooling. */
  data?: Record<string, unknown>;
}

/* ------------------------------ VM Error Model ----------------------------- */

export type VmErrorKind =
  | "ValidationError"
  | "CompileError"
  | "VmError"
  | "Revert"
  | "OOG";

export interface VmError {
  kind: VmErrorKind;
  message: string;
  /** ABI-encoded revert data (if any), raw. */
  data?: Uint8Array;
}

/* --------------------------------- Events ---------------------------------- */

export interface EventLog {
  /** Canonical ABI event name. */
  name: string;
  /** Decoded arguments as a name→value map. */
  args: Record<string, unknown>;
  /** Raw bytes (implementation detail; useful for debugging). */
  raw?: {
    topics?: Hex[];
    data?: Uint8Array;
  };
}

/* ------------------------------ Compile Results ---------------------------- */

export interface CompileSourceRequest {
  /** Full contract source as UTF-8 string. */
  source: string;
  /** Optional file name (for diagnostics). */
  filename?: string;
}

export interface CompileResult {
  /** Compiled IR bytes suitable for the simulator runtime. */
  ir: Uint8Array;
  /** Upper-bound gas estimate for deployment (coarse). */
  gasUpperBound?: number;
  /** Derived ABI (if the compiler can emit it). */
  abi?: Abi;
  /** Content hash of IR or source bundle (tool-specific). */
  codeHash?: Hex;
  diagnostics?: Diagnostic[];
}

/* --------------------------- Simulation: Requests -------------------------- */

export interface SimulateCallRequest {
  manifest: ContractManifest;
  /** Compiled IR bytes (from CompileResult.ir). */
  ir: Uint8Array;
  /** Function to call. */
  method: string;
  /** ABI-typed arguments (already decoded JS values). */
  args: unknown[];
  /** Optional execution context hints (heights, coinbase, gas price). */
  context?: Record<string, unknown>;
}

export interface SimulateDeployRequest {
  manifest: ContractManifest;
  ir: Uint8Array;
  /** Constructor method (if any). */
  method?: string;
  args?: unknown[];
  context?: Record<string, unknown>;
}

/* --------------------------- Simulation: Results --------------------------- */

export interface SimulateResultBase {
  /** Gas consumed by the interpreter (approximate for local sim). */
  gasUsed: number;
  /** Emitted events (decoded using ABI). */
  logs: EventLog[];
  /** Raw return bytes as produced by the VM ABI layer. */
  returnData?: Uint8Array;
  /**
   * Optional decoded return value when the simulator can re-use ABI metadata.
   * For void functions, this may be undefined.
   */
  returnValue?: unknown;
}

export interface SimulateCallOk extends SimulateResultBase {
  ok: true;
}

export interface SimulateCallFail extends SimulateResultBase {
  ok: false;
  error: VmError;
}

export type SimulateCallResult = SimulateCallOk | SimulateCallFail;

export interface SimulateDeployOk extends SimulateResultBase {
  ok: true;
  /** Pseudo-address or deterministic id for the simulated instance. */
  simAddress?: Hex;
  /** Code/content hash surfaced for UX parity. */
  codeHash?: Hex;
}

export interface SimulateDeployFail extends SimulateResultBase {
  ok: false;
  error: VmError;
}

export type SimulateDeployResult = SimulateDeployOk | SimulateDeployFail;

/* ------------------------------ Gas Estimation ----------------------------- */

export interface EstimateGasResult {
  /** Upper-bound estimate (the primary value to display). */
  upper: number;
  /** Optional lower-bound (if available). */
  lower?: number;
  diagnostics?: Diagnostic[];
}

/* ---------------------------------- Export --------------------------------- */

export type {
  // ABI re-exports to make named imports ergonomic from consumers
  AbiParam as ABIParam,
  AbiFunction as ABIFunction,
  AbiEvent as ABIEvent,
  AbiError as ABIError,
  Abi as ABI,
};

