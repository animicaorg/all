/**
 * Shared provider typings for window.animica (AIP-1193-like) and
 * the inpage<->content bridge message envelopes.
 */

/* --------------------------------- Aliases -------------------------------- */

export type Hex = `0x${string}`;

/* ------------------------------ Public (dapp) ------------------------------ */

/** EIP-1193-style request arguments */
export type RequestArguments = {
  method: string;
  params?: unknown[] | Record<string, unknown>;
};

/** Connect info emitted on "connect" */
export type ProviderConnectInfo = {
  chainId: Hex | string;
};

/** "message" event payload (generic) */
export type ProviderMessage = {
  type: string;
  data: unknown;
};

/** Provider event names supported by the extension */
export type ProviderEvent =
  | "connect"
  | "disconnect"
  | "message"
  | "accountsChanged"
  | "chainChanged"
  | "newHeads";

/** Minimal JSON-RPC 2.0 request (legacy shims may use this) */
export type JsonRpcRequest<TParams = any> = {
  jsonrpc: "2.0";
  id: number | string | null;
  method: string;
  params?: TParams;
};

export type JsonRpcSuccess<TResult = any> = {
  jsonrpc: "2.0";
  id: number | string | null;
  result: TResult;
};

export type JsonRpcError = {
  jsonrpc: "2.0";
  id: number | string | null;
  error: {
    code: number | string;
    message: string;
    data?: unknown;
  };
};

export type JsonRpcResponse<TResult = any> = JsonRpcSuccess<TResult> | JsonRpcError;

/* ------------------------- Inpage <-> Content bridge ------------------------ */

export const SOURCE_INPAGE = "animica:inpage" as const;
export const SOURCE_CONTENT = "animica:content" as const;

/** Outgoing request posted by the inpage script to the content script */
export type InpageRequest = {
  source: typeof SOURCE_INPAGE;
  type: "REQUEST";
  id: number;
  payload: {
    method: string;
    params?: unknown;
  };
};

/** Response sent back by the content script to the inpage script */
export type InpageResponse =
  | {
      source: typeof SOURCE_CONTENT;
      type: "RESPONSE";
      id: number;
      result: unknown;
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "RESPONSE";
      id: number;
      error: {
        code?: number | string;
        message: string;
        data?: unknown;
      };
    };

/** Event pushed by the content script to the inpage script */
export type InpageEvent =
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "accountsChanged";
      payload: string[]; // list of addresses (bech32m anim1…)
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "chainChanged";
      payload: { chainId: Hex | string } | (Hex | string);
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "connect";
      payload: ProviderConnectInfo;
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "disconnect";
      payload: { code?: number | string; message?: string } | null;
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "message";
      payload: ProviderMessage;
    }
  | {
      source: typeof SOURCE_CONTENT;
      type: "EVENT";
      event: "newHeads";
      // shape mirrors a light block header summary; kept loose to avoid coupling
      payload: {
        height: number;
        hash: Hex | string;
        parentHash?: Hex | string;
        time?: number; // unix seconds
        gasUsed?: number;
      };
    };

/* ------------------------------- Error codes ------------------------------- */

/**
 * Numeric codes aligned loosely with EIP-1193 / EIP-1474 expectations.
 * Actual error classes are defined in src/provider/errors.ts.
 */
export const RpcErrorCodes = {
  USER_REJECTED: 4001,
  UNAUTHORIZED: 4100,
  UNSUPPORTED: 4200,
  DISCONNECTED: 4900,
  CHAIN_DISCONNECTED: 4901,
  INTERNAL: -32603,
  INVALID_REQUEST: -32600,
  METHOD_NOT_FOUND: -32601,
  INVALID_PARAMS: -32602,
  TIMEOUT: -32000,
} as const;

export type RpcErrorCode = (typeof RpcErrorCodes)[keyof typeof RpcErrorCodes];

/* ---------------------------------- Utils ---------------------------------- */

/** Narrow a window message to bridge types */
export function isBridgeResponse(msg: unknown): msg is InpageResponse {
  return !!msg && (msg as any).source === SOURCE_CONTENT && (msg as any).type === "RESPONSE";
}

export function isBridgeEvent(msg: unknown): msg is InpageEvent {
  return !!msg && (msg as any).source === SOURCE_CONTENT && (msg as any).type === "EVENT";
}
