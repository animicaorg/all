export class AgentError extends Error {
  public readonly code: string;
  public readonly details?: unknown;
  constructor(code: string, message: string, details?: unknown) {
    super(message);
    this.name = "AgentError";
    this.code = code;
    this.details = details;
  }
}

export class RpcError extends AgentError {
  constructor(code: number | string, message: string, details?: unknown) {
    super(`RPC_${code}`, message, details);
    this.name = "RpcError";
  }
}

export class ConfigError extends AgentError {
  constructor(message: string, details?: unknown) {
    super("CONFIG", message, details);
    this.name = "ConfigError";
  }
}

export class PatchError extends AgentError {
  constructor(message: string, details?: unknown) {
    super("PATCH", message, details);
    this.name = "PatchError";
  }
}
