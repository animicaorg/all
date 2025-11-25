/**
 * Content-script bridge:
 * - Listens for page -> content messages from the injected provider (window.animica).
 * - Forwards requests to the background service worker (chrome.runtime.sendMessage).
 * - Forwards background events/responses back to the page via window.postMessage.
 *
 * Message channels:
 *   In-page  → Content:  { source: "animica:inpage",  type: "REQUEST", id, payload }
 *   Content  → In-page:  { source: "animica:content", type: "RESPONSE"|"EVENT", ... }
 *   Content  → BG SW:    chrome.runtime.sendMessage({ channel:"animica", type:"REQUEST", ... })
 *   BG SW    → Content:  chrome.tabs.sendMessage(tabId, { channel:"animica", type:"EVENT"|... })
 */

type InpageRequest = {
  source: "animica:inpage";
  type: "REQUEST";
  id: number;
  // { method: string; params?: any } or similar
  payload: Record<string, unknown>;
};

type InpageResponse =
  | {
      source: "animica:content";
      type: "RESPONSE";
      id: number;
      result: unknown;
    }
  | {
      source: "animica:content";
      type: "RESPONSE";
      id: number;
      error: { code?: number | string; message: string; data?: unknown };
    };

type InpageEvent = {
  source: "animica:content";
  type: "EVENT";
  event: string;
  payload: unknown;
};

type BgRequest = {
  channel: "animica";
  // keep shape minimal; BG router decides what to do
  type: "REQUEST";
  id: number;
  origin: string;
  href: string;
  title?: string;
  payload: Record<string, unknown>;
};

type BgResponse =
  | {
      ok: true;
      id: number;
      // echo origin if BG wants to gate/route per-origin; optional
      origin?: string;
      result: unknown;
    }
  | {
      ok: false;
      id: number;
      origin?: string;
      error: { code?: number | string; message: string; data?: unknown };
    };

type BgEvent = {
  channel: "animica";
  type: "EVENT";
  event: string;
  payload: unknown;
  // optional targeting safety: only forward if matches
  targetOrigin?: string;
};

const SOURCE_INPAGE = "animica:inpage" as const;
const SOURCE_CONTENT = "animica:content" as const;
const CHANNEL_BG = "animica" as const;

/**
 * Install the bridge. Idempotent if called multiple times.
 * Returns a cleanup function.
 */
export function setupContentBridge(): () => void {
  // Avoid duplicate listeners if hot reloaded
  const FLAG = "__animica_content_bridge_installed__";
  if ((window as any)[FLAG]) {
    // no-op cleanup
    return () => void 0;
  }
  Object.defineProperty(window, FLAG, { value: true });

  const onWindowMessage = (ev: MessageEvent) => {
    // Only accept direct messages from this page
    if (ev.source !== window) return;
    const msg = ev.data as InpageRequest | unknown;
    if (!isInpageRequest(msg)) return;

    // Forward to background and bounce the response back to the page
    const bgReq: BgRequest = {
      channel: CHANNEL_BG,
      type: "REQUEST",
      id: msg.id,
      origin: location.origin,
      href: location.href,
      title: safeDocTitle(),
      payload: msg.payload ?? {},
    };

    // chrome.runtime.sendMessage returns a Promise in MV3
    chrome.runtime
      .sendMessage(bgReq)
      .then((resp: BgResponse | void) => {
        // Some BG handlers may respond void; normalize
        const r = resp as BgResponse | undefined;
        if (r && r.ok) {
          postToPage({
            source: SOURCE_CONTENT,
            type: "RESPONSE",
            id: r.id ?? msg.id,
            result: (r as any).result,
          });
        } else if (r && r.ok === false) {
          postToPage({
            source: SOURCE_CONTENT,
            type: "RESPONSE",
            id: r.id ?? msg.id,
            error: normalizeBgError(r.error),
          });
        } else {
          // No structured response: treat as success with undefined result
          postToPage({
            source: SOURCE_CONTENT,
            type: "RESPONSE",
            id: msg.id,
            result: undefined,
          });
        }
      })
      .catch((err: unknown) => {
        postToPage({
          source: SOURCE_CONTENT,
          type: "RESPONSE",
          id: msg.id,
          error: normalizeThrown(err),
        });
      });
  };

  // Listen for background-pushed events (e.g., accountsChanged, chainChanged, newHeads)
  const onRuntimeMessage = (
    msg: unknown,
    _sender: chrome.runtime.MessageSender,
    _sendResponse: (response?: unknown) => void
  ) => {
    const data = msg as BgEvent | BgResponse | unknown;

    // Forward BG events to the page
    if (isBgEvent(data)) {
      if (data.targetOrigin && data.targetOrigin !== location.origin) return;
      const ev: InpageEvent = {
        source: SOURCE_CONTENT,
        type: "EVENT",
        event: data.event,
        payload: data.payload,
      };
      postToPage(ev);
      return; // do not respond
    }

    // Some BG impls may use tabs.sendMessage for responses as well.
    if (isBgResponse(data)) {
      if (data.ok) {
        postToPage({
          source: SOURCE_CONTENT,
          type: "RESPONSE",
          id: data.id,
          result: data.result,
        });
      } else {
        postToPage({
          source: SOURCE_CONTENT,
          type: "RESPONSE",
          id: data.id,
          error: normalizeBgError(data.error),
        });
      }
    }
  };

  window.addEventListener("message", onWindowMessage);
  chrome.runtime.onMessage.addListener(onRuntimeMessage);

  // Expose a small readiness ping for the injected provider (optional)
  try {
    window.dispatchEvent(new Event("animica#content-bridge-ready"));
  } catch {
    /* ignore */
  }

  // Cleanup
  return () => {
    window.removeEventListener("message", onWindowMessage);
    try {
      chrome.runtime.onMessage.removeListener(onRuntimeMessage);
    } catch {
      /* ignore */
    }
    try {
      delete (window as any)[FLAG];
    } catch {
      /* ignore */
    }
  };
}

/* ------------------------------- type guards ------------------------------ */

function isInpageRequest(x: any): x is InpageRequest {
  return !!x && x.source === SOURCE_INPAGE && x.type === "REQUEST" && typeof x.id === "number";
}

function isBgEvent(x: any): x is BgEvent {
  return !!x && x.channel === CHANNEL_BG && x.type === "EVENT";
}

function isBgResponse(x: any): x is BgResponse {
  return !!x && x.id != null && typeof x.ok === "boolean";
}

/* --------------------------------- utils ---------------------------------- */

function postToPage(msg: InpageResponse | InpageEvent): void {
  try {
    window.postMessage(msg, "*");
  } catch {
    // swallow
  }
}

function normalizeThrown(err: any): { code?: number | string; message: string; data?: unknown } {
  if (!err) return { message: "Unknown error" };
  if (typeof err === "string") return { message: err };
  const message = String(err.message || err.toString?.() || "Error");
  const code = err.code;
  const data = err.data;
  return { code, message, data };
}

function normalizeBgError(err: any): { code?: number | string; message: string; data?: unknown } {
  if (!err) return { message: "Background error" };
  if (typeof err === "string") return { message: err };
  return {
    code: err.code,
    message: err.message ?? "Background error",
    data: err.data,
  };
}

function safeDocTitle(): string | undefined {
  try {
    const t = document.title;
    return typeof t === "string" && t.length <= 256 ? t : undefined;
  } catch {
    return undefined;
  }
}
