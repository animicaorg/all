// ENA agent streaming client. Talks to the same-origin broker route
// POST /api/ide/ena which stream-proxies the sidecar /ena/chat SSE.
//
// SSE event types (Inc 2-3 contract):
//   token  {delta}
//   tool   {name, status, summary?}
//   diff   {id, files:[{path, old_text, new_text}]}
//   status {phase, message?}
//   budget {spent_anm, cap, reason}   (cap reached → run stopped cleanly)
//   done   {summary?, calls?, spent_anm?}
//   error  {message}
import { api } from "@/services/api";

export interface DiffFile {
  path: string;
  old_text: string;
  new_text: string;
}

export interface EnaCallbacks {
  onToken?: (delta: string) => void;
  onTool?: (t: { name: string; status: "start" | "done"; summary?: string }) => void;
  onDiff?: (d: { id: string; files: DiffFile[] }) => void;
  onStatus?: (s: { phase: string; message?: string }) => void;
  onBudget?: (b: { spent_anm: number; cap: number; reason?: string }) => void;
  onDone?: (d: { summary?: string; calls?: number; spent_anm?: number }) => void;
  onError?: (message: string) => void;
}

export interface ChatHistoryItem {
  role: string;
  content: string;
}

export interface StreamHandle {
  abort: () => void;
}

export interface StreamOpts {
  // ANM spend cap for this run. The broker uses it (in budget mode) to fund
  // the metered budget and stop the agent when spend reaches the cap.
  cap?: number;
}

// Stream a chat turn. Returns a handle so the caller can stop the stream.
export function streamChat(
  message: string,
  history: ChatHistoryItem[],
  cb: EnaCallbacks,
  opts: StreamOpts = {},
): StreamHandle {
  const controller = new AbortController();

  (async () => {
    let res: Response;
    try {
      const body: Record<string, unknown> = { message, history };
      if (typeof opts.cap === "number" && opts.cap > 0) body.cap = opts.cap;
      res = await fetch("/api/ide/ena", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
    } catch (e: any) {
      if (controller.signal.aborted) return;
      cb.onError?.(e?.message ?? "Failed to reach ENA.");
      return;
    }

    if (!res.ok || !res.body) {
      // Try to surface a JSON error body (e.g. auth gate) cleanly.
      let msg = `ENA request failed (${res.status})`;
      try {
        const data = await res.json();
        msg = data?.error || data?.message || msg;
      } catch {
        /* ignore */
      }
      cb.onError?.(msg);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        let sep: number;
        while ((sep = buf.indexOf("\n\n")) !== -1) {
          const frame = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          dispatchFrame(frame, cb);
        }
      }
      // Flush any trailing frame (in case the stream ended without a blank line).
      if (buf.trim()) dispatchFrame(buf, cb);
    } catch (e: any) {
      if (controller.signal.aborted) return;
      cb.onError?.(e?.message ?? "ENA stream interrupted.");
    }
  })();

  return { abort: () => controller.abort() };
}

function dispatchFrame(frame: string, cb: EnaCallbacks) {
  let event = "message";
  const dataLines: string[] = [];
  for (const raw of frame.split("\n")) {
    const line = raw.replace(/\r$/, "");
    if (!line || line.startsWith(":")) continue;
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).replace(/^ /, ""));
    }
  }
  if (!dataLines.length && event === "message") return;

  let data: any = {};
  const dataStr = dataLines.join("\n");
  if (dataStr) {
    try {
      data = JSON.parse(dataStr);
    } catch {
      data = { raw: dataStr };
    }
  }

  switch (event) {
    case "token":
      if (typeof data.delta === "string") cb.onToken?.(data.delta);
      break;
    case "tool":
      cb.onTool?.(data);
      break;
    case "diff":
      if (data && data.id && Array.isArray(data.files)) cb.onDiff?.(data);
      break;
    case "status":
      cb.onStatus?.(data);
      break;
    case "budget":
      cb.onBudget?.({
        spent_anm: Number(data?.spent_anm ?? 0),
        cap: Number(data?.cap ?? 0),
        reason: data?.reason,
      });
      break;
    case "done":
      cb.onDone?.(data ?? {});
      break;
    case "error":
      cb.onError?.(data?.message ?? "ENA error.");
      break;
    default:
      break;
  }
}

// Approve or reject a pending diff proposal (unblocks the sidecar loop).
export async function approve(id: string, accept: boolean): Promise<{ ok: boolean }> {
  return api.post("/api/ide/ena/approve", { id, accept });
}

// Per-user ENA inference key (each user supplies their own pool key).
export async function keyStatus(): Promise<{ connected: boolean }> {
  return api.get("/api/ide/ena/key");
}
export async function connectKey(key: string): Promise<{ connected: boolean }> {
  return api.post("/api/ide/ena/key", { key });
}
export async function disconnectKey(): Promise<{ ok: boolean }> {
  return api.post("/api/ide/ena/key/disconnect", {});
}

// ANM budget wallet (the "pay with your wallet, capped" flow).
export interface WalletInfo {
  connected: boolean; // user has their own pool key connected (own-key mode)
  balanceAnm: number; // prepaid ENA budget held by the broker for this user
  treasury: string; // ANM deposit recipient
  perCallAnm: number; // metered charge per model call
  defaultCap: number; // suggested default cap
}

// Broker-side budget status: balance, treasury address, per-call price.
export async function getWallet(): Promise<WalletInfo> {
  const r: any = await api.get("/api/ide/ena/wallet");
  return {
    connected: !!r.connected,
    balanceAnm: Number(r.balanceAnm ?? 0),
    treasury: String(r.treasury ?? ""),
    perCallAnm: Number(r.perCallAnm ?? 0),
    defaultCap: Number(r.defaultCap ?? 0),
  };
}

// Confirm an on-chain ANM deposit (the broker reads the amount from the
// chain — we only hand it the tx hash). Returns the new budget balance.
export async function deposit(txid: string): Promise<{ balanceAnm: number }> {
  const r: any = await api.post("/api/ide/ena/deposit", { txid });
  return { balanceAnm: Number(r.balanceAnm ?? 0) };
}

export const enaApi = {
  streamChat,
  approve,
  keyStatus,
  connectKey,
  disconnectKey,
  getWallet,
  deposit,
};
