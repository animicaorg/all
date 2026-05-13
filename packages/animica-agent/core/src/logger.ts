import { redact, safeStringify } from "./safe-json.js";

export type LogLevel = "debug" | "info" | "warn" | "error" | "silent";

const ORDER: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
  silent: 100,
};

export interface Logger {
  level: LogLevel;
  debug(message: string, meta?: unknown): void;
  info(message: string, meta?: unknown): void;
  warn(message: string, meta?: unknown): void;
  error(message: string, meta?: unknown): void;
}

function emit(level: LogLevel, message: string, meta: unknown, current: LogLevel): void {
  if (ORDER[level] < ORDER[current]) return;
  const line = meta === undefined ? message : `${message} ${safeStringify(redact(meta))}`;
  if (level === "error" || level === "warn") {
    process.stderr.write(`[animica-agent] ${level} ${line}\n`);
  } else {
    process.stdout.write(`[animica-agent] ${level} ${line}\n`);
  }
}

export function createLogger(level: LogLevel = "info"): Logger {
  const state: { level: LogLevel } = { level };
  return {
    get level() {
      return state.level;
    },
    set level(v: LogLevel) {
      state.level = v;
    },
    debug: (m, meta) => emit("debug", m, meta, state.level),
    info: (m, meta) => emit("info", m, meta, state.level),
    warn: (m, meta) => emit("warn", m, meta, state.level),
    error: (m, meta) => emit("error", m, meta, state.level),
  };
}
