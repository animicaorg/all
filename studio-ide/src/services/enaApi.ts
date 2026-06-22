// ENA agent streaming client. Talks to the same-origin broker route
// POST /api/ide/ena which stream-proxies the sidecar /ena/chat SSE.
//
// SSE event types (Inc 2-3 contract):
//   token  {delta}
//   tool   {name, status, summary?}
//   diff   {id, files:[{path, old_text, new_text}]}
//   status {phase, message?}
//   done   {summary?}
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
  onDone?: (d: { summary?: string }) => void;
  onError?: (message: string) => void;
}

export interface ChatHistoryItem {
  role: string;
  content: string;
}

export interface StreamHandle {
  abort: () => void;
}

// Stream a chat turn. Returns a handle so the caller can stop the stream.
export function streamChat(
  message: string,
  history: ChatHistoryItem[],
  cb: EnaCallbacks,
): StreamHandle {
  const controller = new AbortController();

  (async () => {
    let res: Response;
    try {
      res = await fetch("/api/ide/ena", {
        method: "POST",
        credentials: "include",
        headers: { "content-type": "application/json", accept: "text/event-stream" },
        body: JSON.stringify({ message, history }),
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

export const enaApi = { streamChat, approve, keyStatus, connectKey, disconnectKey };
