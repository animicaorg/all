/* eslint-disable no-restricted-globals */
/**
 * wsBuffer.worker.ts
 *
 * Purpose:
 *   Coalesce high-frequency WebSocket notification frames into compact UI-oriented
 *   batches to reduce main-thread workload and re-render churn.
 *
 * Usage from main thread:
 *   const worker = new Worker(new URL("./workers/wsBuffer.worker.ts", import.meta.url), { type: "module" });
 *
 *   // optional tuning
 *   worker.postMessage({ type: "config", flushMs: 50, limits: { txs: 2000, logs: 4000 } });
 *
 *   // push already-classified events
 *   worker.postMessage({ type: "pushEvent", event: { kind: "head", id: "chain:1", payload: headObj }});
 *   worker.postMessage({ type: "pushEvent", event: { kind: "tx", id: txHash, payload: txObj }});
 *
 *   // OR push raw JSON-RPC frames (string) to parse+route in the worker:
 *   worker.postMessage({ type: "pushFrame", raw: frameAsString });
 *
 *   // receive coalesced batches
 *   worker.onmessage = (e) => {
 *     if (e.data?.type === "batch") {
 *       const { heads, txs, logs, peers, pings, custom, meta } = e.data;
 *       // update UI stores once per batch
 *     }
 *   };
 */

type InKind = "head" | "tx" | "log" | "peer" | "ping" | "custom";

export interface InEvent<T = any> {
  /** Category for coalescing rules */
  kind: InKind;
  /** Stable identifier for dedupe (txHash, peerId, chainId, logId, etc.) */
  id?: string;
  /** Payload object (UI-usable) */
  payload: T;
  /** Optional timestamp (ms) */
  ts?: number;
}

export interface ConfigMessage {
  type: "config";
  /** Batch flush debounce window (ms) */
  flushMs?: number;
  /** Per-category limits for retained items; older entries will be dropped */
  limits?: Partial<Record<"heads" | "txs" | "logs" | "peers" | "pings" | "custom", number>>;
  /** If true, send periodic 'stats' messages */
  emitStatsEveryMs?: number;
}

export interface PushEventMessage {
  type: "pushEvent";
  event: InEvent;
}

export interface PushEventsMessage {
  type: "pushEvents";
  events: InEvent[];
}

export interface PushFrameMessage {
  type: "pushFrame";
  /** Raw JSON string (from WebSocket `message.data`) */
  raw: string;
}

export interface FlushNowMessage {
  type: "flush";
}

export interface ResetMessage {
  type: "reset";
}

type InMessage =
  | ConfigMessage
  | PushEventMessage
  | PushEventsMessage
  | PushFrameMessage
  | FlushNowMessage
  | ResetMessage;

type BatchOut = {
  type: "batch";
  heads: any[];
  txs: any[];
  logs: any[];
  peers: any[];
  pings: any[];
  custom: any[];
  meta: {
    ts: number;
    counts: Record<string, number>;
    dropped: Record<string, number>;
    windowMs: number;
  };
};

type StatsOut = {
  type: "stats";
  ts: number;
  buffered: Record<string, number>;
  totalReceived: Record<string, number>;
  totalBatches: number;
};

type ErrorOut = {
  type: "error";
  code: string;
  message: string;
  sample?: string;
};

const DEFAULT_LIMITS = {
  heads: 64,
  txs: 5000,
  logs: 8000,
  peers: 2048,
  pings: 4096,
  custom: 4096,
};

let FLUSH_MS = 50;
let STATS_EVERY_MS = 0;

let flushTimer: number | null = null;
let statsTimer: number | null = null;
let lastFlushAt = Date.now();

const limits = { ...DEFAULT_LIMITS };

const buffers = {
  // For heads, keep only latest per chainId (or "default")
  heads: new Map<string, any>(),
  // For txs/logs/peers, keep by id with capped size
  txs: new Map<string, any>(),
  logs: new Map<string, any>(),
  peers: new Map<string, any>(),
  pings: new Map<string, any>(),
  // For misc bursty events, keep a queue (array) but cap overall
  custom: [] as any[],
};

const totalsReceived: Record<string, number> = {
  head: 0,
  tx: 0,
  log: 0,
  peer: 0,
  ping: 0,
  custom: 0,
  frame: 0,
};

let totalBatches = 0;

function scheduleFlush() {
  if (flushTimer !== null) return;
  // @ts-ignore - TS doesn't know WorkerGlobalScope has setTimeout returning number
  flushTimer = setTimeout(() => {
    flushTimer = null;
    doFlush();
  }, FLUSH_MS) as unknown as number;
}

function scheduleStats() {
  if (!STATS_EVERY_MS) return;
  if (statsTimer !== null) return;
  // @ts-ignore
  statsTimer = setInterval(() => {
    const msg: StatsOut = {
      type: "stats",
      ts: Date.now(),
      buffered: {
        heads: buffers.heads.size,
        txs: buffers.txs.size,
        logs: buffers.logs.size,
        peers: buffers.peers.size,
        pings: buffers.pings.size,
        custom: buffers.custom.length,
      },
      totalReceived: { ...totalsReceived },
      totalBatches,
    };
    // @ts-ignore
    self.postMessage(msg);
  }, STATS_EVERY_MS) as unknown as number;
}

function stopStats() {
  if (statsTimer !== null) {
    // @ts-ignore
    clearInterval(statsTimer);
    statsTimer = null;
  }
}

function capMap(map: Map<string, any>, max: number) {
  // Drop oldest entries if above limit (Maps maintain insertion order)
  while (map.size > max) {
    const firstKey = map.keys().next().value;
    if (firstKey === undefined) break;
    map.delete(firstKey);
  }
}

function pushEvent(ev: InEvent) {
  const kind = ev.kind;
  const now = ev.ts ?? Date.now();

  switch (kind) {
    case "head": {
      totalsReceived.head++;
      // choose chain key from payload if present
      const chainKey =
        (ev.payload?.chainId as string | number | undefined)?.toString() ??
        "default";
      const prev = buffers.heads.get(chainKey);
      // keep the higher/most recent head only
      if (!prev) {
        buffers.heads.set(chainKey, ev.payload);
      } else {
        const prevH =
          (prev?.height ??
            prev?.number ??
            prev?.header?.height ??
            0) as number;
        const curH =
          (ev.payload?.height ??
            ev.payload?.number ??
            ev.payload?.header?.height ??
            0) as number;
        if (curH >= prevH) {
          buffers.heads.set(chainKey, ev.payload);
        }
      }
      capMap(buffers.heads, limits.heads);
      break;
    }
    case "tx": {
      totalsReceived.tx++;
      if (!ev.id) {
        // derive id from payload hash-ish fields
        const id =
          (ev.payload?.hash ??
            ev.payload?.txHash ??
            ev.payload?.txid) as string | undefined;
        if (id) {
          buffers.txs.set(id, ev.payload);
        }
      } else {
        buffers.txs.set(ev.id, ev.payload);
      }
      capMap(buffers.txs, limits.txs);
      break;
    }
    case "log": {
      totalsReceived.log++;
      const id =
        ev.id ??
        ((ev.payload?.id ??
          `${ev.payload?.address || "0x"}:${ev.payload?.blockNumber || ev.payload?.block || 0}:${ev.payload?.logIndex || ev.payload?.index || 0}`) as string);
      buffers.logs.set(id, ev.payload);
      capMap(buffers.logs, limits.logs);
      break;
    }
    case "peer": {
      totalsReceived.peer++;
      const id =
        ev.id ??
        ((ev.payload?.id ??
          ev.payload?.peerId ??
          ev.payload?.addr) as string);
      if (id) {
        buffers.peers.set(id, ev.payload);
      }
      capMap(buffers.peers, limits.peers);
      break;
    }
    case "ping": {
      totalsReceived.ping++;
      const id =
        ev.id ?? ((ev.payload?.id ?? ev.payload?.target) as string | undefined);
      if (id) {
        buffers.pings.set(id, ev.payload);
      }
      capMap(buffers.pings, limits.pings);
      break;
    }
    case "custom": {
      totalsReceived.custom++;
      buffers.custom.push(ev.payload);
      if (buffers.custom.length > limits.custom) {
        // drop from the front (oldest)
        buffers.custom.splice(
          0,
          buffers.custom.length - limits.custom,
        );
      }
      break;
    }
    default:
      break;
  }

  // schedule trailing flush
  scheduleFlush();
  return now;
}

/**
 * Try to parse a JSON-RPC notification frame and classify.
 * Very tolerant: best-effort heuristics to find heads/txs/logs.
 */
function pushFrame(raw: string) {
  totalsReceived.frame++;
  let obj: any;
  try {
    obj = JSON.parse(raw);
  } catch (e) {
    emitError("frame_parse_error", "Failed to parse JSON frame", raw);
    return;
  }

  // Common JSON-RPC notify shapes:
  // { jsonrpc, method, params: { subscription, result } }
  // or { method, params } directly
  const payload = obj?.params?.result ?? obj?.result ?? obj?.params ?? obj;

  // Heuristic classification:
  if (looksLikeHead(payload)) {
    pushEvent({
      kind: "head",
      id: (payload?.chainId ?? "default").toString(),
      payload,
    });
    return;
  }
  if (looksLikeTx(payload)) {
    pushEvent({
      kind: "tx",
      id: (payload?.hash ?? payload?.txHash) as string,
      payload,
    });
    return;
  }
  if (looksLikeLog(payload)) {
    const id = `${payload?.address || "0x"}:${payload?.blockNumber || 0}:${payload?.logIndex || 0}`;
    pushEvent({
      kind: "log",
      id,
      payload,
    });
    return;
  }

  // Fallback → custom bucket
  pushEvent({ kind: "custom", payload });
}

function looksLikeHead(obj: any): boolean {
  if (!obj || typeof obj !== "object") return false;
  const hasHash =
    "hash" in obj || "blockHash" in obj || "header" in obj;
  const hasHeight =
    "height" in obj ||
    "number" in obj ||
    (obj.header && ("height" in obj.header || "number" in obj.header));
  const maybeTime =
    "time" in obj ||
    "timestamp" in obj ||
    (obj.header && ("time" in obj.header || "timestamp" in obj.header));
  return !!(hasHash && hasHeight && maybeTime);
}

function looksLikeTx(obj: any): boolean {
  if (!obj || typeof obj !== "object") return false;
  const hasHash = "hash" in obj || "txHash" in obj || "txid" in obj;
  const hasFromTo = "from" in obj || "to" in obj;
  const hasNonceOrIndex = "nonce" in obj || "transactionIndex" in obj;
  return !!(hasHash && (hasFromTo || hasNonceOrIndex));
}

function looksLikeLog(obj: any): boolean {
  if (!obj || typeof obj !== "object") return false;
  const hasAddress = "address" in obj;
  const hasTopics = "topics" in obj && Array.isArray(obj.topics);
  const hasData = "data" in obj;
  return !!(hasAddress && hasTopics && hasData);
}

function doFlush() {
  const now = Date.now();
  const windowMs = now - lastFlushAt;
  lastFlushAt = now;

  // Pull current buffers into arrays and clear/trim appropriately.
  // Heads: ensure stable ordering by height asc (if present)
  const headsArr = Array.from(buffers.heads.values());
  headsArr.sort((a, b) => {
    const ah =
      (a?.height ?? a?.number ?? a?.header?.height ?? 0) as number;
    const bh =
      (b?.height ?? b?.number ?? b?.header?.height ?? 0) as number;
    return ah - bh;
  });

  const txsArr = Array.from(buffers.txs.values());
  const logsArr = Array.from(buffers.logs.values());
  const peersArr = Array.from(buffers.peers.values());
  const pingsArr = Array.from(buffers.pings.values());
  const customArr = buffers.custom.slice();

  // After emitting, clear maps/arrays for the next window.
  buffers.heads.clear();
  buffers.txs.clear();
  buffers.logs.clear();
  buffers.peers.clear();
  buffers.pings.clear();
  buffers.custom.length = 0;

  const dropped = {
    heads: 0, // head map is capped continually
    txs: 0,
    logs: 0,
    peers: 0,
    pings: 0,
    custom: 0,
  };
  // Note: drops are enforced while pushing, so we don't track exact drops here.
  // (If desired, counters could be added above.)

  const out: BatchOut = {
    type: "batch",
    heads: headsArr,
    txs: txsArr,
    logs: logsArr,
    peers: peersArr,
    pings: pingsArr,
    custom: customArr,
    meta: {
      ts: now,
      counts: {
        heads: headsArr.length,
        txs: txsArr.length,
        logs: logsArr.length,
        peers: peersArr.length,
        pings: pingsArr.length,
        custom: customArr.length,
      },
      dropped,
      windowMs,
    },
  };

  // @ts-ignore
  self.postMessage(out);
  totalBatches++;
}

function emitError(code: string, message: string, sample?: string) {
  const out: ErrorOut = { type: "error", code, message, sample };
  // @ts-ignore
  self.postMessage(out);
}

function handleConfig(msg: ConfigMessage) {
  if (typeof msg.flushMs === "number" && isFinite(msg.flushMs) && msg.flushMs >= 0) {
    FLUSH_MS = msg.flushMs;
  }
  if (msg.limits) {
    for (const k of Object.keys(msg.limits) as Array<keyof typeof limits>) {
      const v = msg.limits[k];
      if (typeof v === "number" && v > 0) {
        limits[k] = Math.floor(v);
      }
    }
  }
  if (typeof msg.emitStatsEveryMs === "number") {
    STATS_EVERY_MS = msg.emitStatsEveryMs > 0 ? msg.emitStatsEveryMs : 0;
    stopStats();
    scheduleStats();
  }
}

function resetAll() {
  buffers.heads.clear();
  buffers.txs.clear();
  buffers.logs.clear();
  buffers.peers.clear();
  buffers.pings.clear();
  buffers.custom.length = 0;
  lastFlushAt = Date.now();
}

self.onmessage = (e: MessageEvent<InMessage>) => {
  const data = e.data;
  if (!data || typeof data !== "object") return;

  switch (data.type) {
    case "config":
      handleConfig(data as ConfigMessage);
      break;
    case "pushEvent":
      pushEvent((data as PushEventMessage).event);
      break;
    case "pushEvents":
      for (const ev of (data as PushEventsMessage).events) {
        pushEvent(ev);
      }
      break;
    case "pushFrame":
      pushFrame((data as PushFrameMessage).raw);
      break;
    case "flush":
      // Cancel pending timer and flush immediately
      if (flushTimer !== null) {
        // @ts-ignore
        clearTimeout(flushTimer);
        flushTimer = null;
      }
      doFlush();
      break;
    case "reset":
      resetAll();
      break;
    default:
      // ignore
      break;
  }
};

// Initialize defaults
lastFlushAt = Date.now();
scheduleStats();

export {};
