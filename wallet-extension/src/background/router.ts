/**
 * Background message router for MV3.
 * - Single entrypoint wiring chrome.runtime.onMessage and onConnect (ports).
 * - Callers send { route, payload } and receive { ok, result }.
 * - Handlers are async and receive a context (sender, origin, tabId).
 */

import {
  BgRequest,
  BgResponse,
} from './runtime';

/* eslint-disable no-console */

type Handler<In = unknown, Out = unknown> = (
  payload: In,
  ctx: RouteContext
) => Promise<Out> | Out;

export interface RouteContext {
  sender: chrome.runtime.MessageSender;
  origin: string | null;
  tabId: number | null;
  frameId: number | null;
}

/** Port-based channels (long-lived). We speak the same Req/Res envelope over ports. */
type PortHandler = (msg: BgRequest, port: chrome.runtime.Port, ctx: RouteContext) => Promise<unknown> | unknown;

const routes = new Map<string, Handler<any, any>>();
const portHandlers = new Map<string, PortHandler[]>();
const livePorts = new Map<string, Set<chrome.runtime.Port>>();

let wired = false;

/* --------------------------------- Utils -------------------------------- */

function extractOrigin(sender: chrome.runtime.MessageSender): string | null {
  // MV3 Chrome sets sender.origin for content scripts.
  // Fallback to tab/frame URLs; as last resort, documentUrl.
  const o = (sender as any).origin as string | undefined;
  if (o) return o;
  const url = sender.url || sender.tab?.url || (sender as any).documentUrl;
  if (!url) return null;
  try {
    const u = new URL(url);
    return `${u.protocol}//${u.host}`;
  } catch {
    return null;
  }
}

function buildCtx(sender: chrome.runtime.MessageSender): RouteContext {
  return {
    sender,
    origin: extractOrigin(sender),
    tabId: sender.tab?.id ?? null,
    frameId: (sender as any).frameId ?? null,
  };
}

function ok<T = unknown>(result: T): BgResponse<T> {
  return { ok: true, result };
}
function err(message: string): BgResponse<never> {
  return { ok: false, error: message };
}

/* ------------------------------- API: Routes ----------------------------- */

/** Register a route handler. Throws if duplicate. */
export function addRoute<In = unknown, Out = unknown>(
  route: string,
  handler: Handler<In, Out>
) {
  if (routes.has(route)) throw new Error(`Route already registered: ${route}`);
  routes.set(route, handler as Handler<any, any>);
}

/** Convenience: register multiple at once. */
export function addRoutes(defs: Record<string, Handler<any, any>>) {
  for (const [k, h] of Object.entries(defs)) addRoute(k, h);
}

/* ----------------------------- API: Port channels ------------------------ */

/** Register a handler for messages arriving on a named Port (runtime.connect). */
export function onPort(name: string, handler: PortHandler) {
  const list = portHandlers.get(name) ?? [];
  list.push(handler);
  portHandlers.set(name, list);
}

/** Broadcast a message to all connected ports of a given name. */
export function broadcastPort(name: string, message: unknown) {
  const set = livePorts.get(name);
  if (!set) return;
  for (const p of set) {
    try {
      p.postMessage(message);
    } catch (e) {
      // Drop dead ports
      set.delete(p);
    }
  }
}

/* --------------------------- Internal dispatchers ----------------------- */

async function handleRequest(
  req: BgRequest,
  sender: chrome.runtime.MessageSender
): Promise<BgResponse> {
  if (!req || typeof req !== 'object' || typeof req.route !== 'string') {
    return err('Invalid request: missing route');
  }
  const handler = routes.get(req.route);
  if (!handler) {
    return err(`No handler for route: ${req.route}`);
  }
  const ctx = buildCtx(sender);
  try {
    const result = await handler(req.payload, ctx);
    return ok(result);
  } catch (e: any) {
    const message = e?.message ?? String(e);
    // Include route in error for easier debugging; stacks omitted for safety.
    return err(`[${req.route}] ${message}`);
  }
}

function wireOnMessage() {
  chrome.runtime.onMessage.addListener((req: BgRequest, sender, sendResponse) => {
    // Make it robust across unexpected callers
    const p = handleRequest(req, sender);
    p.then(sendResponse).catch((e) => sendResponse(err(e?.message ?? 'Router error')));
    // Returning true keeps the channel open for async response
    return true;
  });
}

function handlePortConnection(port: chrome.runtime.Port) {
  const name = port.name || 'default';
  const set = livePorts.get(name) ?? new Set<chrome.runtime.Port>();
  set.add(port);
  livePorts.set(name, set);

  const ctxBase = buildCtx((port as any).sender ?? { tab: undefined } as chrome.runtime.MessageSender);

  port.onMessage.addListener(async (msg: BgRequest) => {
    const handlers = portHandlers.get(name) ?? [];
    if (!handlers.length) {
      port.postMessage(err(`No port handlers for "${name}"`));
      return;
    }
    const ctx = { ...ctxBase, sender: (port as any).sender ?? ({} as chrome.runtime.MessageSender) };
    for (const h of handlers) {
      try {
        const out = await h(msg, port, ctx);
        if (out !== undefined) {
          port.postMessage(ok(out));
        }
      } catch (e: any) {
        port.postMessage(err(e?.message ?? 'Port handler error'));
      }
    }
  });

  port.onDisconnect.addListener(() => {
    const s = livePorts.get(name);
    if (s) s.delete(port);
  });
}

function wireOnConnect() {
  chrome.runtime.onConnect.addListener((port) => {
    handlePortConnection(port);
  });
}

/* ------------------------------- Bootstrap ------------------------------ */

function ensureBuiltInRoutes() {
  if (!routes.has('ping')) {
    addRoute<undefined, { pong: true; version: string }>('ping', () => {
      const version = chrome.runtime.getManifest().version ?? '0.0.0';
      return { pong: true, version };
    });
  }
}

/**
 * Initialize the router exactly once. Safe to call multiple times.
 * Registers a default "ping" route for diagnostics.
 */
export function initRouter() {
  if (wired) return;
  wired = true;

  ensureBuiltInRoutes();

  wireOnMessage();
  wireOnConnect();

  // Helpful log in development
  if (process.env.NODE_ENV !== 'production') {
    console.info('[router] initialized with routes:', Array.from(routes.keys()));
  }
}

/**
 * Construct a Router compatible with the background service worker entry.
 * It reuses the same routing tables but defers wiring to the caller.
 */
export function createRouter() {
  ensureBuiltInRoutes();

  return {
    async handleMessage(msg: unknown, sender: chrome.runtime.MessageSender) {
      const resp = await handleRequest(msg as BgRequest, sender);
      if (!resp.ok) throw new Error(resp.error ?? 'Router error');
      return resp.result;
    },
    handlePort(port: chrome.runtime.Port) {
      handlePortConnection(port);
    },
    async onAlarm(_name: string) {
      /* hook reserved for future alarm routes */
    },
    async onStartup() {
      ensureBuiltInRoutes();
    },
  };
}

/* --------------------------------- Types -------------------------------- */

export type { Handler, PortHandler };
