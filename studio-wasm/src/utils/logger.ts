/**
 * logger.ts
 * ----------
 * Tiny, dependency-free, tree-shakeable logger for browser/worker & Node.
 * - Levels: trace, debug, info, warn, error, silent
 * - ISO timestamps
 * - Hierarchical prefixes via logger.child("scope")
 * - Configurable global level and handler sink
 * - Timing helpers: time()/measure()
 */

export type LogLevelName = "trace" | "debug" | "info" | "warn" | "error" | "silent";

const LEVELS: Record<LogLevelName, number> = {
  trace: 10,
  debug: 20,
  info: 30,
  warn: 40,
  error: 50,
  silent: 99,
};

export interface LogMeta {
  ts: Date;
  level: LogLevelName;
  prefix: string[];
}

export type LogHandler = (meta: LogMeta, ...args: unknown[]) => void;

let currentLevel: LogLevelName = (process.env && (process.env.ANIMICA_LOG_LEVEL as LogLevelName)) || "info";

function tsISO(): string {
  // Use toISOString without milliseconds for compactness
  const d = new Date();
  const iso = d.toISOString();
  return iso.replace(/\.\d{3}Z$/, "Z");
}

/* ------------------------------- Default Sink ------------------------------ */

const consoleHandler: LogHandler = (meta, ...args) => {
  const tag = meta.prefix.length ? `[${meta.prefix.join(":")}]` : "";
  const head = `${tsISO()} ${meta.level.toUpperCase()}`;
  const line = tag ? `${head} ${tag}` : head;

  const c = globalThis.console || console;
  switch (meta.level) {
    case "trace":
      c.trace ? c.trace(line, ...args) : c.debug(line, ...args);
      break;
    case "debug":
      c.debug ? c.debug(line, ...args) : c.log(line, ...args);
      break;
    case "info":
      c.info ? c.info(line, ...args) : c.log(line, ...args);
      break;
    case "warn":
      c.warn ? c.warn(line, ...args) : c.log(line, ...args);
      break;
    case "error":
      c.error ? c.error(line, ...args) : c.log(line, ...args);
      break;
    case "silent":
      // no-op
      break;
  }
};

let handler: LogHandler = consoleHandler;

/* --------------------------------- Logger --------------------------------- */

export interface ILogger {
  level(): LogLevelName;
  setLevel(lvl: LogLevelName): void;

  trace(...args: unknown[]): void;
  debug(...args: unknown[]): void;
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;

  /** Create a child logger with an extra prefix scope segment. */
  child(scope: string): ILogger;

  /** Start a timer; call the returned fn to log elapsed with optional suffix args. */
  time(label?: string): () => void;

  /** Measure a sync/async function, logging its duration; returns the function result. */
  measure<T>(label: string, fn: () => T | Promise<T>): Promise<T>;
}

class Logger implements ILogger {
  private readonly prefix: string[];

  constructor(prefix?: string[]) {
    this.prefix = prefix ? [...prefix] : [];
  }

  level(): LogLevelName {
    return currentLevel;
  }

  setLevel(lvl: LogLevelName): void {
    setGlobalLogLevel(lvl);
  }

  private emit(level: LogLevelName, args: unknown[]) {
    if (LEVELS[level] < LEVELS[currentLevel]) return;
    handler(
      {
        ts: new Date(),
        level,
        prefix: this.prefix,
      },
      ...args
    );
  }

  trace = (...args: unknown[]) => this.emit("trace", args);
  debug = (...args: unknown[]) => this.emit("debug", args);
  info = (...args: unknown[]) => this.emit("info", args);
  warn = (...args: unknown[]) => this.emit("warn", args);
  error = (...args: unknown[]) => this.emit("error", args);

  child(scope: string): ILogger {
    const p = scope ? [...this.prefix, scope] : [...this.prefix];
    return new Logger(p);
  }

  time(label = "timer"): () => void {
    const t0 = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
    return () => {
      const t1 = (typeof performance !== "undefined" && performance.now) ? performance.now() : Date.now();
      const ms = t1 - t0;
      this.debug(`${label} +${ms.toFixed(2)}ms`);
    };
  }

  async measure<T>(label: string, fn: () => T | Promise<T>): Promise<T> {
    const stop = this.time(label);
    try {
      const out = await fn();
      return out;
    } finally {
      stop();
    }
  }
}

/* ----------------------------- Global Controls ---------------------------- */

export function setGlobalLogLevel(lvl: LogLevelName): void {
  currentLevel = lvl;
}

export function getGlobalLogLevel(): LogLevelName {
  return currentLevel;
}

export function setLogHandler(h: LogHandler | null): void {
  handler = h ?? consoleHandler;
}

export function getLogHandler(): LogHandler {
  return handler;
}

/* --------------------------------- Factory -------------------------------- */

let rootLogger: ILogger | null = null;

/** Get the root logger (singleton). */
export function getLogger(): ILogger {
  if (!rootLogger) rootLogger = new Logger([]);
  return rootLogger;
}

/** Convenience: scoped child from root. */
export function logger(scope?: string): ILogger {
  return scope ? getLogger().child(scope) : getLogger();
}

/* --------------------------------- Export --------------------------------- */

const api = {
  logger,
  getLogger,
  setGlobalLogLevel,
  getGlobalLogLevel,
  setLogHandler,
  getLogHandler,
  LEVELS,
};

export default api;
