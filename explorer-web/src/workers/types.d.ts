/**
 * Worker message contracts for wsBuffer.worker.ts
 * These ambient declarations provide strong typing for main-thread ↔ worker
 * communications without importing from the worker module directly.
 *
 * Usage (main thread):
 *   const worker = new Worker(new URL("./wsBuffer.worker.ts", import.meta.url), { type: "module" }) as WSBuffer.Worker;
 *   worker.postMessage({ type: "config", flushMs: 50 });
 *   worker.postMessage({ type: "pushEvent", event: { kind: "head", payload: {...} } });
 *   worker.onmessage = (e) => {
 *     if (WSBuffer.isBatch(e.data)) { /* ... */ }
 *     else if (WSBuffer.isStats(e.data)) { /* ... */ }
 *     else if (WSBuffer.isError(e.data)) { /* ... */ }
 *   };
 */

declare namespace WSBuffer {
  /** Categories that the worker can buffer/coalesce. */
  type InKind = "head" | "tx" | "log" | "peer" | "ping" | "custom";

  /** Generic inbound event shape pushed to the worker. */
  interface InEvent<T = any> {
    kind: InKind;
    /** Stable identifier to dedupe/replace (txHash, peerId...). Optional for some kinds. */
    id?: string;
    /** UI-usable payload */
    payload: T;
    /** Optional timestamp (ms since epoch) */
    ts?: number;
  }

  /** Configure batching cadence and per-bucket limits. */
  interface ConfigMessage {
    type: "config";
    /** Batch flush debounce window (ms). Default: 50 */
    flushMs?: number;
    /** Caps for retained buffers; older entries will be dropped. */
    limits?: Partial<Record<"heads" | "txs" | "logs" | "peers" | "pings" | "custom", number>>;
    /** If > 0, the worker will periodically emit 'stats' messages with this interval (ms). */
    emitStatsEveryMs?: number;
  }

  /** Push a single already-classified event. */
  interface PushEventMessage {
    type: "pushEvent";
    event: InEvent;
  }

  /** Push multiple already-classified events. */
  interface PushEventsMessage {
    type: "pushEvents";
    events: InEvent[];
  }

  /** Push a raw JSON(-RPC) frame string; the worker will parse and classify. */
  interface PushFrameMessage {
    type: "pushFrame";
    raw: string;
  }

  /** Ask the worker to flush immediately (cancels any pending debounce). */
  interface FlushNowMessage {
    type: "flush";
  }

  /** Clear all internal buffers and stats counters (where applicable). */
  interface ResetMessage {
    type: "reset";
  }

  /** Union of all inbound messages the worker accepts. */
  type InMessage =
    | ConfigMessage
    | PushEventMessage
    | PushEventsMessage
    | PushFrameMessage
    | FlushNowMessage
    | ResetMessage;

  /** Coalesced batch emitted back to main thread. */
  interface BatchOut {
    type: "batch";
    heads: any[];
    txs: any[];
    logs: any[];
    peers: any[];
    pings: any[];
    custom: any[];
    meta: {
      ts: number;                                // flush timestamp
      counts: Record<"heads" | "txs" | "logs" | "peers" | "pings" | "custom", number>;
      dropped: Record<"heads" | "txs" | "logs" | "peers" | "pings" | "custom", number>;
      windowMs: number;                          // time since previous flush
    };
  }

  /** Optional periodic stats emissions for diagnostics/telemetry. */
  interface StatsOut {
    type: "stats";
    ts: number;
    buffered: Record<"heads" | "txs" | "logs" | "peers" | "pings" | "custom", number>;
    totalReceived: Record<"head" | "tx" | "log" | "peer" | "ping" | "custom" | "frame", number>;
    totalBatches: number;
  }

  /** Error emitted by worker for malformed frames, etc. */
  interface ErrorOut {
    type: "error";
    code: string;     // e.g., "frame_parse_error"
    message: string;
    sample?: string;  // small snippet of offending input (if any)
  }

  /** Union of all outbound messages from the worker. */
  type OutMessage = BatchOut | StatsOut | ErrorOut;

  /** Strongly-typed Worker instance (augment default Worker with message signatures). */
  interface Worker extends globalThis.Worker {
    postMessage(message: InMessage, transfer?: Transferable[]): void;
    onmessage: ((this: Worker, ev: MessageEvent<OutMessage>) => any) | null;
    addEventListener(
      type: "message",
      listener: (this: Worker, ev: MessageEvent<OutMessage>) => any,
      options?: boolean | AddEventListenerOptions
    ): void;
    removeEventListener(
      type: "message",
      listener: (this: Worker, ev: MessageEvent<OutMessage>) => any,
      options?: boolean | EventListenerOptions
    ): void;
  }

  /** Type guards for convenient runtime narrowing. */
  function isBatch(msg: unknown): msg is BatchOut;
  function isStats(msg: unknown): msg is StatsOut;
  function isError(msg: unknown): msg is ErrorOut;
}

/**
 * Provide minimal declarations so importing workers with Vite/Webpack patterns is type-safe.
 * Use ONE of the following in app code:
 *   new Worker(new URL("./wsBuffer.worker.ts", import.meta.url), { type: "module" }) as WSBuffer.Worker
 * or (if using ?worker plugin):
 *   import WsWorker from "./wsBuffer.worker.ts?worker";
 *   const worker = new WsWorker() as WSBuffer.Worker;
 */

/** Vite/rollup "?worker" plugin module signature */
declare module "*?worker" {
  const WorkerFactory: {
    new (): Worker;
  };
  export default WorkerFactory;
}

/** Generic "*.worker.ts" module signature for loaders that allow direct import. */
declare module "*.worker.ts" {
  const WorkerFactory: {
    new (): Worker;
  };
  export default WorkerFactory;
}

export {};
