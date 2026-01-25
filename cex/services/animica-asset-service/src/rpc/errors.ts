/**
 * Animica RPC Error Classes
 */

export class RpcError extends Error {
  constructor(
    message: string,
    public code?: number,
    public data?: any
  ) {
    super(message);
    this.name = "RpcError";
  }
}

export class LocalRpcError extends RpcError {
  constructor(message: string, cause?: Error) {
    super(message, -32603);
    this.name = "LocalRpcError";
    this.cause = cause;
  }
}

export class MethodNotFoundError extends RpcError {
  constructor(method: string) {
    super(`Method not found: ${method}`, -32601);
    this.name = "MethodNotFoundError";
  }
}

export class InvalidParamsError extends RpcError {
  constructor(message: string) {
    super(message, -32602);
    this.name = "InvalidParamsError";
  }
}

export class TimeoutError extends RpcError {
  constructor(method: string, timeout: number) {
    super(`RPC call ${method} timed out after ${timeout}ms`, -32000);
    this.name = "TimeoutError";
  }
}

export class RateLimitError extends RpcError {
  constructor(message: string) {
    super(message, -32005);
    this.name = "RateLimitError";
  }
}

export class NodeUnavailableError extends RpcError {
  constructor(message: string, cause?: Error) {
    super(message, -32001);
    this.name = "NodeUnavailableError";
    this.cause = cause;
  }
}

export function isRetryableError(error: Error): boolean {
  if (error instanceof TimeoutError) return true;
  if (error instanceof NodeUnavailableError) return true;
  if (error instanceof RateLimitError) return true;
  if (error instanceof LocalRpcError && error.message.includes("ECONNREFUSED")) return true;
  if (error instanceof LocalRpcError && error.message.includes("ETIMEDOUT")) return true;
  return false;
}
