/**
 * Minimal namespaced logger for the wallet extension (MV3-safe).
 *
 * Features:
 *  - Levels: debug, info, warn, error, silent
 *  - Namespace filtering via patterns (like `debug`) from:
 *      - localStorage key: "animica:debug"  e.g. "wallet-*,provider,background"
 *      - Vite env: import.meta.env.VITE_DEBUG
 *  - Global level from:
 *      - window.__ANIMICA_LOG_LEVEL (for quick dev toggling)
 *      - localStorage key: "animica:loglevel"
 *      - import.meta.env.MODE: "production" -> "warn", else "info"
 *  - Timers: time()/timeEnd() using performance.now()
 *  - Pluggable reporter hook to forward records to external sinks (e.g., Sentry)
 *
 * Safe in:
 *  - MV3 background service worker (no DOM assumptions)
 *  - Content scripts / UI pages
 *  - Unit tests (falls back gracefully if window/chrome unavailable)
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error' | 'silent';

export interface LogRecord {
  ts: number;                 // ms epoch
  ns: string;                 // namespace
  level: LogLevel;
  msg: string;
  data?: unknown[];
}

export type LogReporter = (rec: LogRecord) => void;

const LEVEL_ORDER: Record<Exclude<LogLevel, 'silent'>, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function nowMs(): number {
  // Use performance.timeOrigin + now() for higher resolution if available
  try {
    if (typeof performance !== 'undefined' && performance.now) {
      return Math.floor((performance.timeOrigin ?? Date.now()) + performance.now());
    }
  } catch { /* noop */ }
  return Date.now();
}

/* --------------------------------- Env read -------------------------------- */

function safeGetLocalStorage(key: string): string | undefined {
  try {
    if (typeof localStorage !== 'undefined') {
      const v = localStorage.getItem(key);
      return v === null ? undefined : v;
    }
  } catch { /* blocked or unavailable */ }
  return undefined;
}

function readGlobalLevel(): LogLevel {
  // @ts-ignore
  const fromGlobal = (globalThis as any).__ANIMICA_LOG_LEVEL as LogLevel | undefined;
  if (fromGlobal) return fromGlobal;
  const fromLS = safeGetLocalStorage('animica:loglevel') as LogLevel | undefined;
  if (fromLS) return fromLS;

  // Vite env guard
  let mode: string | undefined;
  try {
    // @ts-ignore
    mode = import.meta?.env?.MODE;
  } catch { /* not a Vite build or not accessible */ }

  // In tests default to warn to reduce noise
  const isTest =
    typeof process !== 'undefined' &&
    (process.env?.VITEST || process.env?.JEST_WORKER_ID || process.env?.NODE_ENV === 'test');

  if (isTest) return 'warn';
  return mode === 'production' ? 'warn' : 'info';
}

function readDebugPatterns(): string[] {
  const ls = safeGetLocalStorage('animica:debug');
  let vite: string | undefined;
  try {
    // @ts-ignore
    vite = import.meta?.env?.VITE_DEBUG as string | undefined;
  } catch { /* ignore */ }
  const raw = (vite || ls || '').trim();
  if (!raw) return [];
  return raw
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function compilePattern(pat: string): RegExp {
  // Convert "wallet-*", "provider", "*" → regex
  const esc = pat.replace(/[-/\\^$+?.()|[\]{}]/g, '\\$&').replace(/\*/g, '.*');
  return new RegExp(`^${esc}$`);
}

const DEBUG_PATTERNS = readDebugPatterns();
const DEBUG_REGEXES = DEBUG_PATTERNS.map(compilePattern);

function nsEnabled(ns: string): boolean {
  if (DEBUG_REGEXES.length === 0) return true; // if no patterns, allow all namespaces
  return DEBUG_REGEXES.some((re) => re.test(ns));
}

/* -------------------------------- Reporter --------------------------------- */

let reporter: LogReporter | undefined;

/** Install an optional reporter (e.g., to forward errors). */
export function setReporter(r: LogReporter | undefined) {
  reporter = r;
}

/* --------------------------------- Logger ---------------------------------- */

export interface Logger {
  readonly ns: string;
  getLevel(): LogLevel;
  setLevel(lvl: LogLevel): void;

  debug(msg: string, ...data: unknown[]): void;
  info(msg: string, ...data: unknown[]): void;
  warn(msg: string, ...data: unknown[]): void;
  error(msg: string, ...data: unknown[]): void;

  /** Start a timer; call timeEnd with the same label to log duration. */
  time(label?: string): void;
  timeEnd(label?: string, msg?: string, ...data: unknown[]): void;

  /** Create a child logger with an extended namespace (ns:child). */
  child(suffix: string): Logger;

  /** Log only once per unique key for this logger instance. */
  once(key: string, level: LogLevel, msg: string, ...data: unknown[]): void;
}

class LoggerImpl implements Logger {
  readonly ns: string;
  private level: LogLevel;
  private timers = new Map<string, number>();
  private onceKeys = new Set<string>();

  constructor(ns: string, level: LogLevel) {
    this.ns = ns;
    this.level = level;
  }

  getLevel(): LogLevel {
    return this.level;
  }

  setLevel(lvl: LogLevel): void {
    this.level = lvl;
  }

  child(suffix: string): Logger {
    const childNs = suffix ? `${this.ns}:${suffix}` : this.ns;
    return new LoggerImpl(childNs, this.level);
  }

  private shouldLog(level: LogLevel): boolean {
    if (level === 'silent') return false;
    const current = this.level;
    if (current === 'silent') return false;
    // Namespace gate
    if (!nsEnabled(this.ns)) return false;
    return LEVEL_ORDER[level as Exclude<LogLevel, 'silent'>] >= LEVEL_ORDER[current as Exclude<LogLevel, 'silent'>];
  }

  private emit(level: LogLevel, msg: string, args: unknown[]) {
    const t = nowMs();
    const rec: LogRecord = { ts: t, ns: this.ns, level, msg, data: args };

    // Pretty prefix: [hh:mm:ss.mmm] ns level
    const d = new Date(t);
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    const prefix = `[${h}:${m}:${s}.${ms}] ${this.ns} ${level}:`;

    // Route to appropriate console method
    const c = (globalThis as any).console;
    try {
      switch (level) {
        case 'debug':
          c?.debug ? c.debug(prefix, msg, ...args) : c?.log?.(prefix, msg, ...args);
          break;
        case 'info':
          c?.info ? c.info(prefix, msg, ...args) : c?.log?.(prefix, msg, ...args);
          break;
        case 'warn':
          c?.warn ? c.warn(prefix, msg, ...args) : c?.log?.(prefix, msg, ...args);
          break;
        case 'error':
          c?.error ? c.error(prefix, msg, ...args) : c?.log?.(prefix, msg, ...args);
          break;
      }
    } catch { /* console may be stubbed */ }

    // Forward to reporter if present
    try {
      reporter?.(rec);
    } catch { /* ignore reporter failures */ }
  }

  debug(msg: string, ...data: unknown[]): void {
    if (this.shouldLog('debug')) this.emit('debug', msg, data);
  }
  info(msg: string, ...data: unknown[]): void {
    if (this.shouldLog('info')) this.emit('info', msg, data);
  }
  warn(msg: string, ...data: unknown[]): void {
    if (this.shouldLog('warn')) this.emit('warn', msg, data);
  }
  error(msg: string, ...data: unknown[]): void {
    if (this.shouldLog('error')) this.emit('error', msg, data);
  }

  time(label: string = 'default'): void {
    this.timers.set(label, (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now());
  }

  timeEnd(label: string = 'default', msg: string = 'completed', ...data: unknown[]): void {
    const start = this.timers.get(label);
    const end = (typeof performance !== 'undefined' && performance.now) ? performance.now() : Date.now();
    const dur = typeof start === 'number' ? end - start : undefined;
    this.timers.delete(label);
    if (dur == null) {
      this.warn(`timer "${label}" ended without start`);
      return;
    }
    this.info(`${msg} (${label})`, { duration_ms: +dur.toFixed(3) }, ...data);
  }

  once(key: string, level: LogLevel, msg: string, ...data: unknown[]): void {
    const k = `${this.ns}::${key}`;
    if (this.onceKeys.has(k)) return;
    this.onceKeys.add(k);
    switch (level) {
      case 'debug': this.debug(msg, ...data); break;
      case 'info': this.info(msg, ...data); break;
      case 'warn': this.warn(msg, ...data); break;
      case 'error': this.error(msg, ...data); break;
    }
  }
}

/* ------------------------------- Public API -------------------------------- */

let rootLevel: LogLevel = readGlobalLevel();

/** Change global default level for newly created loggers (existing instances unaffected). */
export function setDefaultLevel(lvl: LogLevel) {
  rootLevel = lvl;
}

/** Create a logger for a given namespace. */
export function createLogger(namespace: string): Logger {
  return new LoggerImpl(namespace, rootLevel);
}

/** Convenience default logger for the whole extension. */
export const log = createLogger('wallet');

/* ----------------------------- Error utilities ----------------------------- */

/** Serialize unknown error into a safe object for logging or reporting. */
export function serializeError(err: unknown): Record<string, unknown> {
  if (err instanceof Error) {
    return {
      name: err.name,
      message: err.message,
      stack: err.stack,
      cause: (err as any).cause ? serializeError((err as any).cause) : undefined,
    };
  }
  if (typeof err === 'object' && err != null) {
    try {
      return JSON.parse(JSON.stringify(err as any));
    } catch {
      return { message: String(err) };
    }
  }
  return { message: String(err) };
}

/** Wrap a function to log any thrown error with context, then rethrow. */
export function withErrorLogging<TArgs extends any[], TReturn>(
  logger: Logger,
  fn: (...args: TArgs) => TReturn,
  contextMsg?: string
): (...args: TArgs) => TReturn {
  return ((...args: TArgs) => {
    try {
      return fn(...args);
    } catch (e) {
      logger.error(contextMsg ?? 'Unhandled error', serializeError(e));
      throw e;
    }
  }) as (...args: TArgs) => TReturn;
}
