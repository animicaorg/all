/**
 * Minimal async queue for MV3/browser environments.
 * - FIFO by default; optional numeric priority (lower = sooner)
 * - Concurrency control
 * - Per-task timeout and AbortSignal cancellation
 * - Pause/Resume and onIdle()
 */

export class CancelledError extends Error {
  constructor(message = 'Task cancelled') {
    super(message);
    this.name = 'CancelledError';
  }
}

export class TimeoutError extends Error {
  constructor(message = 'Task timed out') {
    super(message);
    this.name = 'TimeoutError';
  }
}

export type Task<T> = () => Promise<T> | T;

export interface QueueOptions {
  /** Max number of tasks running simultaneously (default 1 = strict FIFO). */
  concurrency?: number;
  /** Called when a task rejects and the caller didn't handle it (rare). */
  onUncaughtError?: (err: unknown) => void;
}

export interface EnqueueOptions {
  /** Lower runs sooner. Defaults to 0. */
  priority?: number;
  /** AbortSignal to cancel the queued/running task. */
  signal?: AbortSignal;
  /** Per-task timeout; rejects with TimeoutError if exceeded. */
  timeoutMs?: number;
  /** Optional label for debug logs. */
  label?: string;
}

type Resolver<T> = (value: T | PromiseLike<T>) => void;
type Rejecter = (reason?: unknown) => void;

interface QueueItem<T> {
  fn: Task<T>;
  opts: Required<Pick<EnqueueOptions, 'priority'>> & Omit<EnqueueOptions, 'priority'>;
  enqueuedAt: number;
  resolve: Resolver<T>;
  reject: Rejecter;
}

export class AsyncQueue {
  private queue: QueueItem<unknown>[] = [];
  private running = 0;
  private paused = false;
  private readonly concurrency: number;
  private readonly onUncaughtError?: (err: unknown) => void;

  private idleResolve: (() => void) | null = null;
  private idlePromise: Promise<void> | null = null;

  constructor(opts: QueueOptions = {}) {
    this.concurrency = Math.max(1, Math.floor(opts.concurrency ?? 1));
    this.onUncaughtError = opts.onUncaughtError;
  }

  /** Number of tasks waiting to start. */
  get size(): number {
    return this.queue.length;
  }

  /** Number of tasks currently running. */
  get pending(): number {
    return this.running;
  }

  /** Whether the queue is paused (no new tasks will start). */
  get isPaused(): boolean {
    return this.paused;
  }

  /** Pause starting new tasks (running ones continue). */
  pause(): void {
    this.paused = true;
  }

  /** Resume starting new tasks. */
  resume(): void {
    if (!this.paused) return;
    this.paused = false;
    this.pump();
  }

  /**
   * Enqueue a task. Returns a promise with the task's result.
   * If an AbortSignal is provided and is already aborted, rejects immediately.
   */
  add<T>(fn: Task<T>, options: EnqueueOptions = {}): Promise<T> {
    const opts: QueueItem<T>['opts'] = {
      priority: options.priority ?? 0,
      signal: options.signal,
      timeoutMs: options.timeoutMs,
      label: options.label,
    };

    if (opts.signal?.aborted) {
      return Promise.reject(new CancelledError(`${opts.label ?? 'task'}: aborted before enqueue`));
    }

    return new Promise<T>((resolve, reject) => {
      const item: QueueItem<T> = {
        fn,
        opts,
        enqueuedAt: Date.now(),
        resolve,
        reject,
      };
      this.queue.push(item as unknown as QueueItem<unknown>);
      // Keep queue roughly ordered by priority (stable-ish insertion sort)
      if (this.queue.length > 1 && opts.priority !== 0) {
        // Move the newly pushed item left while lower priority than predecessor.
        let i = this.queue.length - 1;
        while (i > 0 && (this.queue[i - 1].opts.priority ?? 0) > (opts.priority ?? 0)) {
          const tmp = this.queue[i - 1];
          this.queue[i - 1] = this.queue[i];
          this.queue[i] = tmp;
          i--;
        }
      }
      queueMicrotask(() => this.pump());
    });
  }

  /** Wait until the queue becomes empty and all running tasks finish. */
  onIdle(): Promise<void> {
    if (this.size === 0 && this.running === 0) return Promise.resolve();
    if (!this.idlePromise) {
      this.idlePromise = new Promise<void>((res) => (this.idleResolve = res));
    }
    return this.idlePromise;
  }

  /** Cancel all queued (not yet running) tasks. Running tasks are not cancelled. */
  clear(reason: string = 'Queue cleared'): void {
    const err = new CancelledError(reason);
    const toReject = this.queue.splice(0, this.queue.length);
    for (const item of toReject) {
      item.reject(err);
    }
    this.maybeResolveIdle();
  }

  /** Internal: start tasks while capacity is available. */
  private pump(): void {
    if (this.paused) return;
    while (this.running < this.concurrency && this.queue.length > 0) {
      const next = this.queue.shift()!;
      this.start(next);
    }
    this.maybeResolveIdle();
  }

  /** Internal: start a single task with timeout/abort handling. */
  private start<T>(item: QueueItem<T>): void {
    this.running++;

    const { signal, timeoutMs, label } = item.opts;

    let timeoutId: number | undefined;
    let aborted = false;

    const onAbort = () => {
      aborted = true;
      item.reject(new CancelledError(`${label ?? 'task'} aborted`));
    };

    if (signal) {
      signal.addEventListener('abort', onAbort, { once: true });
    }

    const wrap = async () => {
      if (timeoutMs && timeoutMs > 0) {
        timeoutId = (setTimeout(() => {
          item.reject(new TimeoutError(`${label ?? 'task'} exceeded ${timeoutMs} ms`));
        }, timeoutMs) as unknown) as number;
      }
      try {
        const res = await item.fn();
        if (!aborted) item.resolve(res);
      } catch (err) {
        if (!aborted) item.reject(err);
        else this.onUncaughtError?.(err);
      } finally {
        if (timeoutId !== undefined) clearTimeout(timeoutId as unknown as number);
        if (signal) signal.removeEventListener('abort', onAbort);
        this.running--;
        // Schedule further tasks; avoid deep recursion
        queueMicrotask(() => this.pump());
      }
    };

    // Fire and forget; result resolution handled via item.resolve/reject
    void wrap();
  }

  /** Resolve idle promise if applicable. */
  private maybeResolveIdle(): void {
    if (this.size === 0 && this.running === 0 && this.idleResolve) {
      const res = this.idleResolve;
      this.idleResolve = null;
      this.idlePromise = null;
      res();
    }
  }
}

/* Convenience: a singleton per-module queue (FIFO) for low-throughput pipelines */
let defaultQueue: AsyncQueue | null = null;

/** Get a shared FIFO queue (concurrency=1). */
export function getDefaultQueue(): AsyncQueue {
  if (!defaultQueue) defaultQueue = new AsyncQueue({ concurrency: 1 });
  return defaultQueue;
}
