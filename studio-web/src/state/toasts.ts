/**
 * Global toast notifications slice.
 *
 * Usage from components:
 *   const { info, success, warning, error, dismiss, clear, toasts } = useStore(s => s);
 *   info("Compiled successfully");
 *   const id = error("Deploy failed", { title: "Deploy", timeoutMs: 8000 });
 *   dismiss(id);
 */

import { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';

export type ToastKind = 'info' | 'success' | 'warning' | 'error';

export interface ToastAction {
  label: string;
  href?: string;            // optional link target
  onClick?: () => void;     // optional callback (ephemeral; not persisted)
}

export interface Toast {
  id: string;
  kind: ToastKind;
  message: string;
  title?: string;
  ts: number;               // created at (ms since epoch)
  timeoutMs?: number;       // auto-dismiss timer; if undefined or 0 and sticky=false, defaults apply
  sticky?: boolean;         // if true, never auto-dismiss
  action?: ToastAction;
}

export interface ToastsSlice {
  toasts: Toast[];
  maxToasts: number;

  notify(input: {
    kind: ToastKind;
    message: string;
    title?: string;
    timeoutMs?: number;
    sticky?: boolean;
    action?: ToastAction;
    id?: string;
  }): string;

  info(message: string, opts?: Omit<Parameters<ToastsSlice['notify']>[0], 'kind' | 'message'>): string;
  success(message: string, opts?: Omit<Parameters<ToastsSlice['notify']>[0], 'kind' | 'message'>): string;
  warning(message: string, opts?: Omit<Parameters<ToastsSlice['notify']>[0], 'kind' | 'message'>): string;
  error(message: string, opts?: Omit<Parameters<ToastsSlice['notify']>[0], 'kind' | 'message'>): string;

  dismiss(id: string): void;
  clear(): void;
}

// ----- internal helpers -----

const timers = new Map<string, number>();

function uid(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    try { return crypto.randomUUID(); } catch {}
  }
  // fallback: 16 random hex + ts
  let r = '';
  if (typeof crypto !== 'undefined' && 'getRandomValues' in crypto) {
    const b = new Uint8Array(8);
    crypto.getRandomValues(b);
    r = Array.from(b).map(x => x.toString(16).padStart(2, '0')).join('');
  } else {
    r = Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0') +
        Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0');
  }
  return `${r}-${Date.now()}`;
}

function scheduleAutoDismiss(id: string, ms: number, dismiss: (id: string) => void) {
  if (ms <= 0) return;
  clearTimer(id);
  const t = (typeof window !== 'undefined' ? window : globalThis) as any;
  const handle = t.setTimeout(() => {
    timers.delete(id);
    try { dismiss(id); } catch { /* noop */ }
  }, ms);
  timers.set(id, handle);
}

function clearTimer(id: string) {
  const t = timers.get(id);
  if (t) {
    const g = (typeof window !== 'undefined' ? window : globalThis) as any;
    g.clearTimeout?.(t);
    timers.delete(id);
  }
}

// ----- slice implementation -----

const DEFAULT_TIMEOUT_MS = 5000;

const createToastsSlice: SliceCreator<ToastsSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  toasts: [],
  maxToasts: 5,

  notify(input) {
    const id = input.id ?? uid();
    const ts = Date.now();
    const sticky = Boolean(input.sticky);
    const timeoutMs = Number(
      input.timeoutMs ?? (sticky ? 0 : DEFAULT_TIMEOUT_MS)
    );

    const toast: Toast = {
      id,
      kind: input.kind,
      message: input.message,
      title: input.title,
      ts,
      timeoutMs,
      sticky,
      action: input.action,
    };

    set((s: any) => {
      const next = [...(s.toasts as Toast[]), toast];
      // enforce cap by dropping oldest
      while (next.length > (s.maxToasts ?? 5)) next.shift();
      return { toasts: next };
    });

    if (!sticky && timeoutMs > 0) {
      scheduleAutoDismiss(id, timeoutMs, (toastId) => get().dismiss(toastId));
    }

    return id;
  },

  info(message, opts)    { return get().notify({ kind: 'info', message, ...(opts ?? {}) }); },
  success(message, opts) { return get().notify({ kind: 'success', message, ...(opts ?? {}) }); },
  warning(message, opts) { return get().notify({ kind: 'warning', message, ...(opts ?? {}) }); },
  error(message, opts)   { return get().notify({ kind: 'error', message, ...(opts ?? {}) }); },

  dismiss(id) {
    clearTimer(id);
    set((s: any) => ({ toasts: (s.toasts as Toast[]).filter(t => t.id !== id) }));
  },

  clear() {
    // clear all timers
    for (const id of Array.from(timers.keys())) clearTimer(id);
    set({ toasts: [] } as Partial<StoreState>);
  },
});

registerSlice<ToastsSlice>(createToastsSlice);

// No default export to avoid accidental component imports creating multiple stores.
export default undefined;
