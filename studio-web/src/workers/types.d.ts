/**
 * Typed message contracts for web workers used in Studio Web.
 * Currently focused on the formatter worker.
 */

/** Languages supported by the formatter worker. */
export type FormatterLang =
  | 'typescript'
  | 'javascript'
  | 'json'
  | 'css'
  | 'html'
  | 'markdown'
  | 'yaml'
  | 'python';

/** Request shape sent to the formatter worker. */
export interface FormatRequest {
  id: string | number;
  lang: FormatterLang;
  code: string;
  /** Additional Prettier options (if applicable to the language). */
  options?: Record<string, unknown>;
}

/** Successful response from the formatter worker. */
export interface FormatSuccess {
  id: string | number;
  ok: true;
  formatted: string;
  /** Optional diagnostics or non-fatal notes (e.g., fallbacks used). */
  diagnostics?: string[];
}

/** Failure response from the formatter worker. */
export interface FormatFailure {
  id: string | number;
  ok: false;
  error: string;
}

/** Union response type. */
export type FormatResponse = FormatSuccess | FormatFailure;

/**
 * Strongly-typed interface for the formatter worker instance.
 * You can still use a regular `Worker`, but this narrows message types.
 */
export interface FormatterWorker extends Worker {
  postMessage(message: FormatRequest, transfer?: Transferable[]): void;

  onmessage: ((this: FormatterWorker, ev: MessageEvent<FormatResponse>) => any) | null;

  addEventListener(
    type: 'message',
    listener: (this: FormatterWorker, ev: MessageEvent<FormatResponse>) => any,
    options?: boolean | AddEventListenerOptions
  ): void;
}

/** Type guard for FormatResponse at runtime. */
export function isFormatResponse(msg: unknown): msg is FormatResponse;

/** Type guard for FormatSuccess at runtime. */
export function isFormatSuccess(msg: unknown): msg is FormatSuccess;

/** Type guard for FormatFailure at runtime. */
export function isFormatFailure(msg: unknown): msg is FormatFailure;

/** Implementation of the runtime type guards (only types in d.ts; actual impl is provided by app code). */
declare global {
  interface Window {
    /** Optional namespace for worker typings (no runtime value required). */
    __ANIMICA_STUDIO_WORKERS__?: unknown;
  }
}

/**
 * Common module shims used by bundlers for worker imports.
 * - If you import with `?worker`, this declaration helps TS understand it returns a constructor.
 * - You might also create workers with `new URL('./formatter.worker.ts', import.meta.url)`, which needs no shim.
 */
declare module '*?worker' {
  const WorkerFactory: {
    new (options?: WorkerOptions): Worker;
  };
  export default WorkerFactory;
}
