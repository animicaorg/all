/**
 * Provider error types aligned with the AIP-1193/EIP-1193 style.
 * These are used by the in-page provider (window.animica) and by the
 * content/background bridge when responding to dapp requests.
 */

import { RpcErrorCodes } from "./types";
import type { RpcErrorCode } from "./types";

/** Base class for provider/RPC errors */
export class ProviderError extends Error {
  readonly code: RpcErrorCode;
  readonly data?: unknown;

  constructor(code: RpcErrorCode, message: string, data?: unknown) {
    super(message);
    this.code = code;
    this.data = data;
    this.name = new.target.name;
    // Maintains proper stack in TS/JS
    if (typeof (Error as any).captureStackTrace === "function") {
      (Error as any).captureStackTrace(this, new.target);
    }
  }

  toJSON(): { code: RpcErrorCode; message: string; data?: unknown; name: string } {
    const j: any = { code: this.code, message: this.message, name: this.name };
    if (this.data !== undefined) j.data = this.data;
    return j;
  }
}

/* --------------------------- Specific error types -------------------------- */

export class UserRejectedRequestError extends ProviderError {
  constructor(message = "User rejected the request.", data?: unknown) {
    super(RpcErrorCodes.USER_REJECTED, message, data);
  }
}

export class UnauthorizedError extends ProviderError {
  constructor(message = "The requested method or account has not been authorized.", data?: unknown) {
    super(RpcErrorCodes.UNAUTHORIZED, message, data);
  }
}

export class UnsupportedMethodError extends ProviderError {
  constructor(message = "The requested method is not supported by this provider.", data?: unknown) {
    super(RpcErrorCodes.UNSUPPORTED, message, data);
  }
}

export class DisconnectedError extends ProviderError {
  constructor(message = "The provider is disconnected.", data?: unknown) {
    super(RpcErrorCodes.DISCONNECTED, message, data);
  }
}

export class ChainDisconnectedError extends ProviderError {
  constructor(message = "The provider is not connected to the requested chain.", data?: unknown) {
    super(RpcErrorCodes.CHAIN_DISCONNECTED, message, data);
  }
}

export class TimeoutError extends ProviderError {
  constructor(message = "The request timed out.", data?: unknown) {
    super(RpcErrorCodes.TIMEOUT, message, data);
  }
}

export class InvalidRequestError extends ProviderError {
  constructor(message = "Invalid JSON-RPC request.", data?: unknown) {
    super(RpcErrorCodes.INVALID_REQUEST, message, data);
  }
}

export class MethodNotFoundError extends ProviderError {
  constructor(message = "Method not found.", data?: unknown) {
    super(RpcErrorCodes.METHOD_NOT_FOUND, message, data);
  }
}

export class InvalidParamsError extends ProviderError {
  constructor(message = "Invalid params for method.", data?: unknown) {
    super(RpcErrorCodes.INVALID_PARAMS, message, data);
  }
}

export class InternalError extends ProviderError {
  constructor(message = "Internal error.", data?: unknown) {
    super(RpcErrorCodes.INTERNAL, message, data);
  }
}

/* --------------------------------- Helpers -------------------------------- */

export function isProviderError(e: unknown): e is ProviderError {
  return !!e && typeof e === "object" && "code" in (e as any) && "message" in (e as any);
}

/** Create the closest matching error class for a code */
export function errorForCode(
  code: RpcErrorCode,
  message?: string,
  data?: unknown
): ProviderError {
  switch (code) {
    case RpcErrorCodes.USER_REJECTED:
      return new UserRejectedRequestError(message, data);
    case RpcErrorCodes.UNAUTHORIZED:
      return new UnauthorizedError(message, data);
    case RpcErrorCodes.UNSUPPORTED:
      return new UnsupportedMethodError(message, data);
    case RpcErrorCodes.DISCONNECTED:
      return new DisconnectedError(message, data);
    case RpcErrorCodes.CHAIN_DISCONNECTED:
      return new ChainDisconnectedError(message, data);
    case RpcErrorCodes.TIMEOUT:
      return new TimeoutError(message, data);
    case RpcErrorCodes.INVALID_REQUEST:
      return new InvalidRequestError(message, data);
    case RpcErrorCodes.METHOD_NOT_FOUND:
      return new MethodNotFoundError(message, data);
    case RpcErrorCodes.INVALID_PARAMS:
      return new InvalidParamsError(message, data);
    case RpcErrorCodes.INTERNAL:
    default:
      return new InternalError(message, data);
  }
}

/** Wrap an unknown thrown value into a ProviderError */
export function wrapError(err: unknown, fallbackMessage = "Internal error"): ProviderError {
  if (isProviderError(err)) return err;
  if (err && typeof err === "object") {
    const anyErr = err as any;
    if (typeof anyErr.code !== "undefined" && typeof anyErr.message === "string") {
      return errorForCode(anyErr.code as RpcErrorCode, anyErr.message, anyErr.data);
    }
    if (anyErr.message && typeof anyErr.message === "string") {
      return new InternalError(anyErr.message, anyErr.data);
    }
  }
  if (typeof err === "string") {
    return new InternalError(err);
  }
  return new InternalError(fallbackMessage, { cause: err as any });
}

/** Serialize an error into a JSON-RPC compatible payload */
export function serializeError(e: unknown): {
  code: RpcErrorCode;
  message: string;
  data?: unknown;
} {
  const err = wrapError(e);
  const out: any = { code: err.code, message: err.message };
  if (err.data !== undefined) out.data = err.data;
  return out;
}

/** Convenience: throw a specific ProviderError by code */
export function throwRpcError(code: RpcErrorCode, message?: string, data?: unknown): never {
  throw errorForCode(code, message, data);
}
