/**
 * Worker postMessage contracts (type-only).
 * These mirror the protocol used by src/worker/pyvm.worker.ts and are
 * consumed by the main-thread APIs (simulator/compiler) for strong typing.
 *
 * If you extend operations in the worker, update the maps below.
 */

export type WorkerOp =
  | 'PING'
  | 'INIT'
  | 'COMPILE_SOURCE'
  | 'COMPILE_IR'
  | 'LINK_MANIFEST'
  | 'CREATE_STATE'
  | 'DISPOSE_STATE'
  | 'SIMULATE_CALL'
  | 'SIMULATE_DEPLOY'
  | 'ESTIMATE_GAS';

export type RequestEnvelope<Op extends string = string, P = unknown> = {
  /** Correlates request/response; should be unique per in-flight request. */
  id: string;
  /** Operation discriminator. */
  op: Op;
  /** Operation-specific payload. */
  payload: P;
};

export type SuccessEnvelope<
  Op extends string = string,
  _P = unknown,
  R = unknown
> = {
  id: string;
  op: Op;
  ok: true;
  /** Operation-specific result. */
  result: R;
};

export type ErrorEnvelope<Op extends string = string> = {
  id: string;
  op: Op;
  ok: false;
  /** Human-readable error string; do not rely on exact wording. */
  error: string;
  /** Optional stack for debugging (not guaranteed). */
  stack?: string;
};

export interface WorkerPayloads {
  PING: { nonce?: number };
  INIT: { baseURL?: string; preloadPackages?: string[] };
  COMPILE_SOURCE: { manifest: unknown; source: string };
  COMPILE_IR: { manifest: unknown; irBytes: Uint8Array };
  LINK_MANIFEST: { manifest: unknown; codeHash?: string };
  CREATE_STATE: {};
  DISPOSE_STATE: { stateId: string };
  SIMULATE_CALL: {
    compiled: unknown;
    manifest: unknown;
    entry: string;
    args: Record<string, unknown>;
    stateId?: string;
  };
  SIMULATE_DEPLOY: {
    manifest: unknown;
    source: string;
    constructor?: string;
    args?: Record<string, unknown>;
    stateId?: string;
  };
  ESTIMATE_GAS: {
    compiled: unknown;
    manifest: unknown;
    entry: string;
    args: Record<string, unknown>;
  };
}

export interface WorkerResults {
  PING: { pong: true; nonce?: number };
  INIT: { ready: true; pyodideVersion?: string; python?: string };
  COMPILE_SOURCE: { compiled: unknown; irSize?: number; codeHash?: string };
  COMPILE_IR: { compiled: unknown; irSize?: number; codeHash?: string };
  LINK_MANIFEST: { linked: unknown; codeHash?: string };
  CREATE_STATE: { stateId: string };
  DISPOSE_STATE: { disposed: true };
  SIMULATE_CALL: {
    ok: true;
    returnValue: unknown;
    gasUsed?: number;
    events?: Array<{ name: string; args: Record<string, unknown> }>;
  };
  SIMULATE_DEPLOY: {
    ok: true;
    address?: string;
    gasUsed?: number;
    events?: Array<{ name: string; args: Record<string, unknown> }>;
  };
  ESTIMATE_GAS: { gasUpperBound: number };
}

/** Discriminated-union of all valid worker requests. */
export type WorkerRequest = {
  [K in WorkerOp]: RequestEnvelope<K, WorkerPayloads[K]>;
}[WorkerOp];

/** Discriminated-union of all valid worker responses (success or error). */
export type WorkerResponse = {
  [K in WorkerOp]:
    | SuccessEnvelope<K, WorkerPayloads[K], WorkerResults[K]>
    | ErrorEnvelope<K>;
}[WorkerOp];

/** Convenience aliases for message events. */
export type WorkerMessageEvent = MessageEvent<WorkerResponse>;
export type MainMessageEvent = MessageEvent<WorkerRequest>;

/**
 * Ambient module declaration for the dedicated worker entry.
 * This allows `new Worker(new URL('../worker/pyvm.worker.ts', import.meta.url), { type: 'module' })`
 * to have typed postMessage/onmessage shapes in TS.
 */
declare module '../worker/pyvm.worker.ts' {
  export default class PyVMWorker extends Worker {
    postMessage(message: WorkerRequest, transfer?: Transferable[]): void;
    onmessage: ((this: Worker, ev: MessageEvent<WorkerResponse>) => any) | null;
    addEventListener(
      type: 'message',
      listener: (ev: MessageEvent<WorkerResponse>) => any,
      options?: boolean | AddEventListenerOptions
    ): void;
  }
}
