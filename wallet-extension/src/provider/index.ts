/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Animica in-page provider (AIP-1193-like).
 *
 * Loaded directly into the webpage context by the content script injector.
 * Communicates with the extension background via the content-script bridge using
 * window.postMessage.
 *
 * Message format:
 *   Page -> Content:
 *     { source: "animica:inpage", type: "REQUEST", id, payload: { method, params? } }
 *
 *   Content -> Page (response):
 *     { source: "animica:content", type: "RESPONSE", id, result? | error? }
 *
 *   Content -> Page (event):
 *     { source: "animica:content", type: "EVENT", event, payload }
 */

type RequestArguments = {
  method: string;
  params?: unknown[] | Record<string, unknown>;
};

type ProviderConnectInfo = {
  chainId: string; // hex string like "0x1" or network tag (kept as string)
};

type ProviderMessage = {
  type: string;
  data: unknown;
};

type ProviderEvent =
  | "connect"
  | "disconnect"
  | "message"
  | "accountsChanged"
  | "chainChanged"
  | "newHeads"; // extension-specific stream

type JsonRpcResult = { jsonrpc: "2.0"; id: number; result?: any; error?: any };

type JsonRpcRequest<TParams = any> = {
  jsonrpc: "2.0";
  id: number | string;
  method: string;
  params?: TParams;
};

type InpageRequest = {
  source: "animica:inpage";
  type: "REQUEST";
  id: number;
  payload: { method: string; params?: any };
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

const SOURCE_CONTENT = "animica:content" as const;
const SOURCE_INPAGE = "animica:inpage" as const;

class ProviderRpcError extends Error {
  code: number | string;
  data?: unknown;
  constructor(code: number | string, message: string, data?: unknown) {
    super(message);
    this.code = code;
    this.data = data;
    this.name = "ProviderRpcError";
  }
}

const enum RpcErrors {
  USER_REJECTED = 4001,
  UNAUTHORIZED = 4100,
  UNSUPPORTED = 4200,
  DISCONNECTED = 4900,
  CHAIN_DISCONNECTED = 4901,
  INTERNAL = -32603,
  INVALID_REQUEST = -32600,
  METHOD_NOT_FOUND = -32601,
  INVALID_PARAMS = -32602,
  TIMEOUT = -32000,
}

function createError(code: number | string, message: string, data?: unknown) {
  return new ProviderRpcError(code, message, data);
}

/* --------------------------------- Emitter -------------------------------- */

type Listener = (...args: any[]) => void;

class TinyEmitter {
  private listeners = new Map<string, Set<Listener>>();

  on(event: string, fn: Listener): this {
    if (!this.listeners.has(event)) this.listeners.set(event, new Set());
    this.listeners.get(event)!.add(fn);
    return this;
  }
  once(event: string, fn: Listener): this {
    const wrap: Listener = (...args) => {
      this.removeListener(event, wrap);
      fn(...args);
    };
    return this.on(event, wrap);
  }
  removeListener(event: string, fn: Listener): this {
    this.listeners.get(event)?.delete(fn);
    return this;
  }
  removeAllListeners(event?: string): this {
    if (event) this.listeners.delete(event);
    else this.listeners.clear();
    return this;
  }
  emit(event: string, ...args: any[]): boolean {
    const ls = this.listeners.get(event);
    if (!ls || ls.size === 0) return false;
    [...ls].forEach((fn) => {
      try {
        fn(...args);
      } catch (err) {
        // swallow handler errors to avoid breaking provider
        setTimeout(() => {
          throw err;
        });
      }
    });
    return true;
  }
}

/* -------------------------- Test-friendly factory ------------------------- */

/**
 * Lightweight provider factory used by unit tests. Allows injecting a transport
 * that speaks raw JSON-RPC without the window.postMessage bridge.
 */
export function createProvider(transport: { send: (req: JsonRpcRequest) => Promise<any> }) {
  const emitter = new TinyEmitter();
  let nextId = 1;

  const provider: any = {
    isAnimica: true,
    request: async (args: { method: string; params?: any }) => {
      if (!args || typeof args.method !== "string") {
        throw createError(RpcErrors.INVALID_REQUEST, "Invalid request arguments");
      }
      const id = nextId++;
      const req: JsonRpcRequest = {
        jsonrpc: "2.0",
        id,
        method: args.method,
        params: args.params ?? [],
      };
      try {
        const res = await transport.send(req);
        if (res && typeof res === "object") {
          if ((res as any).error) {
            const e = (res as any).error;
            throw createError(e.code ?? RpcErrors.INTERNAL, e.message ?? "Provider error", e.data);
          }
          if ("result" in (res as any)) return (res as any).result;
        }
        return (res as any)?.result ?? res;
      } catch (err) {
        if (err instanceof ProviderRpcError) throw err;
        if (err && typeof err === "object" && "code" in (err as any) && "message" in (err as any)) {
          throw createError((err as any).code, (err as any).message, (err as any).data);
        }
        const msg = err instanceof Error ? err.message : String(err);
        throw createError(RpcErrors.TIMEOUT, msg);
      }
    },
    on: (event: ProviderEvent, listener: Listener) => {
      emitter.on(event, listener);
      return provider;
    },
    removeListener: (event: ProviderEvent, listener: Listener) => {
      emitter.removeListener(event, listener);
      return provider;
    },
    __testEmit: (event: ProviderEvent, payload: unknown) => {
      emitter.emit(event, payload as any);
    },
  };

  return provider;
}

/* -------------------------------- Provider -------------------------------- */

type Pending = {
  resolve: (v: any) => void;
  reject: (e: any) => void;
  timer?: number;
  method: string;
};

const DEFAULT_TIMEOUT_MS = 30_000;

export class AnimicaProvider extends TinyEmitter {
  public readonly isAnimica = true;
  public readonly isConnectedFlag = { value: false };

  // EIP-1193 compatibility fields (best-effort)
  public chainId: string | null = null; // hex string when known
  public networkVersion: string | null = null; // decimal string when known
  public selectedAddress: string | null = null; // first account if any
  public accounts: string[] = [];

  private _reqId = 1;
  private _pending = new Map<number, Pending>();

  constructor() {
    super();
    this._onWindowMessage = this._onWindowMessage.bind(this);
    window.addEventListener("message", this._onWindowMessage, false);

    // Mark as injected / ready for dapps that probe
    (window as any).animica = this;
    try {
      window.dispatchEvent(new Event("animica#initialized"));
    } catch {
      /* noop */
    }
  }

  /* ---------------------------- Public API (1193) ---------------------------- */

  /**
   * request: primary method entry.
   */
  async request(args: RequestArguments): Promise<any> {
    if (!args || typeof args !== "object" || typeof args.method !== "string") {
      throw createError(RpcErrors.INVALID_REQUEST, "Invalid request arguments");
    }
    const { method } = args;
    const params = (args as any).params;

    // Special-case shims
    if (method === "eth_requestAccounts" || method === "wallet_requestPermissions" || method === "animica_requestAccounts") {
      const res = await this._rpc({ method: "animica_requestAccounts", params });
      // Update local mirror
      if (Array.isArray(res)) {
        this.accounts = res;
        this.selectedAddress = res[0] ?? null;
        this.isConnectedFlag.value = true;
        this.emit("accountsChanged", [...this.accounts]);
        this.emit("connect", { chainId: this.chainId ?? "0x0" } as ProviderConnectInfo);
      }
      return res;
    }

    if (method === "eth_accounts" || method === "animica_accounts") {
      const res = await this._rpc({ method: "animica_accounts", params });
      if (Array.isArray(res)) {
        this.accounts = res;
        this.selectedAddress = res[0] ?? null;
      }
      return res;
    }

    if (method === "eth_chainId" || method === "animica_chainId") {
      const res = await this._rpc({ method: "animica_chainId", params });
      if (typeof res === "string") {
        this.chainId = res;
      }
      return res;
    }

    // Pass-through
    return this._rpc({ method, params });
  }

  /**
   * Legacy send / sendAsync shims (for older dapps).
   */
  send(method: string | { method: string; params?: any; id?: number }, params?: any): Promise<any> | JsonRpcResult {
    // send(payload, callback) form is handled by sendAsync. Here we support (method, params)
    if (typeof method === "string") {
      return this.request({ method, params });
    }
    const payload = method as any;
    // Synchronous result is not supported; always async
    return {
      jsonrpc: "2.0",
      id: payload.id ?? 0,
      error: { code: RpcErrors.UNSUPPORTED, message: "Synchronous send is not supported. Use request()." },
    };
  }

  sendAsync(payload: any, cb: (err: any, result?: any) => void): void {
    const method = payload?.method;
    const params = payload?.params;
    this.request({ method, params })
      .then((result) => cb(null, { jsonrpc: "2.0", id: payload.id ?? 0, result }))
      .catch((err) => cb(err));
  }

  isConnected(): boolean {
    return !!this.isConnectedFlag.value;
  }

  // Event typings (AIP-1193-like)
  override on(event: ProviderEvent, listener: Listener): this {
    return super.on(event, listener);
  }
  override once(event: ProviderEvent, listener: Listener): this {
    return super.once(event, listener);
  }
  override removeListener(event: ProviderEvent, listener: Listener): this {
    return super.removeListener(event, listener);
  }

  // Convenience; some dapps call .enable()
  async enable(): Promise<string[]> {
    const accs = await this.request({ method: "animica_requestAccounts" });
    return Array.isArray(accs) ? accs : [];
  }

  // Cleanup (rarely needed on webpages, but good for tests)
  destroy(): void {
    window.removeEventListener("message", this._onWindowMessage, false);
    this.removeAllListeners();
    this._pending.forEach((p) => p.reject(createError(RpcErrors.DISCONNECTED, "Provider destroyed")));
    this._pending.clear();
  }

  /* --------------------------------- Internals -------------------------------- */

  private _rpc({ method, params }: { method: string; params?: any }): Promise<any> {
    const id = this._reqId++;
    const req: InpageRequest = {
      source: SOURCE_INPAGE,
      type: "REQUEST",
      id,
      payload: { method, params },
    };

    return new Promise<any>((resolve, reject) => {
      const timer = (setTimeout(() => {
        this._pending.delete(id);
        reject(createError(RpcErrors.TIMEOUT, `Request timed out: ${method}`));
      }, DEFAULT_TIMEOUT_MS) as unknown) as number;

      this._pending.set(id, { resolve, reject, timer, method });
      window.postMessage(req, "*");
    });
  }

  private _onWindowMessage = (ev: MessageEvent) => {
    const msg = ev.data as InpageResponse | InpageEvent | unknown;
    // Only messages from our content bridge
    if (!msg || (msg as any).source !== SOURCE_CONTENT) return;

    // Handle response
    if ((msg as any).type === "RESPONSE") {
      const id = (msg as any).id as number;
      const pending = this._pending.get(id);
      if (!pending) return;
      if (pending.timer) clearTimeout(pending.timer);

      if ((msg as any).error) {
        const e = (msg as any).error;
        const err = createError(e.code ?? RpcErrors.INTERNAL, e.message ?? "Provider error", e.data);
        pending.reject(err);
      } else {
        const result = (msg as any).result;
        this._mirrorStateIfRelevant(pending.method, result);
        pending.resolve(result);
      }
      this._pending.delete(id);
      return;
    }

    // Handle event
    if ((msg as any).type === "EVENT") {
      const event = (msg as any).event as string;
      const payload = (msg as any).payload;

      switch (event) {
        case "accountsChanged": {
          if (Array.isArray(payload)) {
            this.accounts = payload as string[];
            this.selectedAddress = this.accounts[0] ?? null;
            this.isConnectedFlag.value = this.accounts.length > 0;
          }
          this.emit("accountsChanged", payload);
          break;
        }
        case "chainChanged": {
          if (typeof payload?.chainId === "string") {
            this.chainId = payload.chainId;
          } else if (typeof payload === "string") {
            this.chainId = payload;
          }
          this.emit("chainChanged", this.chainId);
          break;
        }
        case "connect": {
          const info = (payload ?? { chainId: this.chainId ?? "0x0" }) as ProviderConnectInfo;
          if (info.chainId) this.chainId = info.chainId;
          this.isConnectedFlag.value = true;
          this.emit("connect", info);
          break;
        }
        case "disconnect": {
          this.isConnectedFlag.value = false;
          this.emit("disconnect", payload);
          break;
        }
        case "message": {
          const m = payload as ProviderMessage;
          this.emit("message", m);
          break;
        }
        case "newHeads": {
          // extension-specific convenience stream (forward directly)
          this.emit("newHeads", payload);
          break;
        }
        default: {
          // Unknown events are forwarded as generic message
          this.emit("message", { type: event, data: payload } as ProviderMessage);
        }
      }
    }
  };

  private _mirrorStateIfRelevant(method: string, result: any) {
    // Keep local mirrors for common methods
    if (method === "animica_chainId" || method === "eth_chainId") {
      if (typeof result === "string") this.chainId = result;
    } else if (method === "animica_accounts" || method === "eth_accounts" || method === "animica_requestAccounts" || method === "eth_requestAccounts") {
      if (Array.isArray(result)) {
        this.accounts = result;
        this.selectedAddress = result[0] ?? null;
        this.isConnectedFlag.value = this.accounts.length > 0;
      }
    }
  }
}

/* ------------------------------ Auto-injector ------------------------------ */

// If another wallet already injected, prefer first-in by default.
// However, we expose window.animica if not present.
(function inject() {
  try {
    // Do not overwrite an existing provider
    if ((window as any).animica) return;

    const provider = new AnimicaProvider();
    // defineProperty to make it non-enumerable and immutable-ish
    Object.defineProperty(window, "animica", {
      value: provider,
      writable: false,
      enumerable: false,
      configurable: false,
    });

    // Also expose for EIP-1193-esque probing
    Object.defineProperty(window, "animicaProvider", {
      value: provider,
      writable: false,
      enumerable: false,
      configurable: false,
    });

    // Inform dapps that rely on a DOM event to detect providers
    window.dispatchEvent(new Event("animica#initialized"));
  } catch {
    // ignore
  }
})();

export default AnimicaProvider;
