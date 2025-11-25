import { useCallback, useEffect, useMemo, useRef, useState } from "react";

/**
 * useHead — subscribe to newHeads over JSON-RPC WebSocket.
 *
 * This hook manages a resilient WS connection with exponential backoff
 * and surfaces the latest head plus connection status.
 *
 * It assumes a JSON-RPC shape like:
 *   -> {"id":1,"method":"subscribe","params":["newHeads"]}
 *   <- {"id":1,"result":"sub_id"}                // subscription id
 *   <- {"method":"subscription","params":{"subscription":"sub_id","result":{...head}}}
 *
 * If your node uses a different method name (e.g. "animica_subscribe"),
 * pass it via options.subscribeMethod.
 */

export type Head = {
  height: number;
  hash: string;
  parentHash?: string;
  time?: string;
  timestamp?: number;
  stateRoot?: string;
  txs?: number;
  [k: string]: unknown;
};

type Status = "idle" | "connecting" | "open" | "closed" | "error";

export interface UseHeadOptions {
  /** If omitted, we infer from window.__ANIMICA_RPC_URL__ or location */
  wsUrl?: string;
  /** JSON-RPC subscription method name. Default: "subscribe" */
  subscribeMethod?: string;
  /** Topic name for heads. Default: "newHeads" */
  topic?: string;
  /** Autostart on mount. Default: true */
  autoStart?: boolean;
  /** Max reconnect delay (ms). Default: 10000 */
  maxBackoffMs?: number;
  /** Called for every new head (after parsing) */
  onHead?: (head: Head) => void;
  /** Optional transform for raw payload -> Head */
  transform?: (raw: any) => Head;
  /** Optional headers to include in `Sec-WebSocket-Protocol` or query (JWT, API key, etc.) */
  authToken?: string;
}

/** Convert http(s) -> ws(s) */
function httpToWs(url: string): string {
  const u = new URL(url);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.toString();
}

function defaultInferWsUrl(): string | undefined {
  // Prefer a global injected by the app/env:
  const anyWin = window as any;
  const injected = anyWin.__ANIMICA_WS_URL__ || anyWin.__ANIMICA_RPC_WS__;
  if (typeof injected === "string") return injected;

  const rpcHttp = anyWin.__ANIMICA_RPC_URL__ as string | undefined;
  if (rpcHttp) return httpToWs(rpcHttp);

  // Fallback: if app is served alongside RPC at same origin with /ws path
  try {
    const guess = new URL("/ws", window.location.origin);
    return guess.toString();
  } catch {
    return undefined;
  }
}

function parseHead(raw: any): Head {
  // Try to be permissive and normalize common shapes
  const height =
    typeof raw?.height === "number"
      ? raw.height
      : typeof raw?.number === "number"
      ? raw.number
      : typeof raw?.height === "string"
      ? parseInt(raw.height, 10)
      : typeof raw?.number === "string"
      ? parseInt(raw.number, 10)
      : NaN;

  const ts =
    typeof raw?.timestamp === "number"
      ? raw.timestamp
      : typeof raw?.timestamp === "string"
      ? Number(raw.timestamp)
      : typeof raw?.time === "string"
      ? Date.parse(raw.time)
      : undefined;

  return {
    height,
    hash: raw?.hash ?? raw?.blockHash ?? "",
    parentHash: raw?.parentHash ?? raw?.parent_block_hash,
    time: typeof raw?.time === "string" ? raw.time : undefined,
    timestamp: Number.isFinite(ts) ? ts : undefined,
    stateRoot: raw?.stateRoot,
    txs: typeof raw?.txs === "number" ? raw.txs : undefined,
    ...raw,
  };
}

export function useHead(options: UseHeadOptions = {}) {
  const {
    wsUrl: wsUrlProp,
    subscribeMethod = "subscribe",
    topic = "newHeads",
    autoStart = true,
    maxBackoffMs = 10_000,
    onHead,
    transform,
    authToken,
  } = options;

  const inferredUrl = useMemo(() => wsUrlProp ?? defaultInferWsUrl(), [wsUrlProp]);
  const [status, setStatus] = useState<Status>(autoStart ? "connecting" : "idle");
  const [error, setError] = useState<string | null>(null);
  const [head, setHead] = useState<Head | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const subIdRef = useRef<string | null>(null);
  const idCounter = useRef<number>(1);
  const backoffAttempt = useRef<number>(0);
  const reconnectTimer = useRef<number | null>(null);
  const heartbeatTimer = useRef<number | null>(null);
  const wantedOpen = useRef<boolean>(autoStart);

  const clearTimers = () => {
    if (reconnectTimer.current) {
      window.clearTimeout(reconnectTimer.current);
      reconnectTimer.current = null;
    }
    if (heartbeatTimer.current) {
      window.clearTimeout(heartbeatTimer.current);
      heartbeatTimer.current = null;
    }
  };

  const scheduleReconnect = useCallback(() => {
    if (!wantedOpen.current) return;
    const base = 500; // ms
    const delay = Math.min(maxBackoffMs, base * Math.pow(2, backoffAttempt.current));
    backoffAttempt.current = Math.min(backoffAttempt.current + 1, 10);
    setStatus("connecting");
    reconnectTimer.current = window.setTimeout(() => {
      connect();
    }, delay);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxBackoffMs, subscribeMethod, topic, inferredUrl, authToken]);

  const send = (obj: any) => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    ws.send(JSON.stringify(obj));
  };

  const ping = () => {
    // Application-level heartbeat: no-op "ping" request to keep idle connections alive
    const id = idCounter.current++;
    send({ jsonrpc: "2.0", id, method: "ping" });
    heartbeatTimer.current = window.setTimeout(ping, 25_000);
  };

  const subscribe = () => {
    subIdRef.current = null;
    const id = idCounter.current++;
    send({ jsonrpc: "2.0", id, method: subscribeMethod, params: [topic] });

    const onMessage = (ev: MessageEvent) => {
      try {
        const msg = JSON.parse(ev.data);
        // Subscription ack
        if (msg?.id === id && msg?.result && typeof msg.result === "string") {
          subIdRef.current = msg.result;
          setStatus("open");
          setError(null);
          backoffAttempt.current = 0;
          // start heartbeat once fully subscribed
          if (!heartbeatTimer.current) heartbeatTimer.current = window.setTimeout(ping, 25_000);
          return;
        }
        // Notifications
        if (
          msg?.method === "subscription" &&
          msg?.params?.subscription &&
          subIdRef.current &&
          msg.params.subscription === subIdRef.current
        ) {
          const payload = msg.params.result;
          const parsed = transform ? transform(payload) : parseHead(payload);
          setHead(parsed);
          onHead?.(parsed);
          return;
        }
      } catch (e) {
        // ignore malformed
      }
    };

    const ws = wsRef.current;
    if (ws) ws.addEventListener("message", onMessage);

    // Cleanup this temporary listener when socket closes or is replaced
    const detach = () => {
      if (ws) ws.removeEventListener("message", onMessage);
    };
    return detach;
  };

  const connect = useCallback(() => {
    clearTimers();
    // Already open?
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return;

    if (!inferredUrl) {
      setStatus("error");
      setError("WS URL is not configured.");
      return;
    }

    try {
      setStatus("connecting");
      setError(null);

      // Attach auth token via subprotocol or query param
      let url = inferredUrl;
      let protocols: string[] | undefined = undefined;
      if (authToken) {
        try {
          const asUrl = new URL(inferredUrl);
          asUrl.searchParams.set("auth", authToken);
          url = asUrl.toString();
          // Optionally: protocols = ["bearer", authToken]; // if server expects it
        } catch {
          // ignore
        }
      }

      const ws = new WebSocket(url, protocols);
      wsRef.current = ws;

      ws.addEventListener("open", () => {
        setStatus("open");
        const detach = subscribe();
        // When this ws closes, also detach the temp message handler
        ws.addEventListener("close", () => detach && detach(), { once: true });
      });

      ws.addEventListener("error", (ev) => {
        setStatus("error");
        setError("WebSocket error");
        try {
          ws.close();
        } catch {}
      });

      ws.addEventListener("close", () => {
        setStatus("closed");
        clearTimers();
        subIdRef.current = null;
        // schedule reconnect
        scheduleReconnect();
      });
    } catch (e: any) {
      setStatus("error");
      setError(e?.message ?? "Failed to open WebSocket");
      scheduleReconnect();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inferredUrl, authToken, subscribeMethod, topic, maxBackoffMs]);

  const start = useCallback(() => {
    if (wantedOpen.current) return;
    wantedOpen.current = true;
    connect();
  }, [connect]);

  const stop = useCallback(() => {
    wantedOpen.current = false;
    clearTimers();
    const ws = wsRef.current;
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      try {
        ws.close(1000, "client-stop");
      } catch {}
    }
    wsRef.current = null;
    subIdRef.current = null;
    setStatus("idle");
  }, []);

  const reconnect = useCallback(() => {
    // Force a reconnect attempt now
    stop();
    wantedOpen.current = true;
    backoffAttempt.current = 0;
    connect();
  }, [connect, stop]);

  // Autostart/cleanup on mount/unmount
  useEffect(() => {
    if (autoStart) {
      wantedOpen.current = true;
      connect();
    }
    return () => {
      stop();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inferredUrl, subscribeMethod, topic, authToken]);

  return {
    /** Latest head (or null until first arrives) */
    head,
    /** Connection status */
    status,
    /** Error message (if any) */
    error,
    /** Start the subscription (no-op if already started) */
    start,
    /** Stop the subscription and close the socket */
    stop,
    /** Force an immediate reconnect */
    reconnect,
    /** The URL in use (resolved) */
    wsUrl: inferredUrl,
  };
}

export default useHead;
