/**
 * Animica Explorer — Global Toasts Store
 * -----------------------------------------------------------------------------
 * Lightweight, production-ready toast manager using Zustand.
 * - Typed toasts with intents: info | success | warning | error
 * - Auto-dismiss with sensible defaults (overridable)
 * - Stable IDs (crypto.randomUUID fallback)
 * - Bounded queue with newest-first ordering
 * - Imperative helpers: toast.info/success/warning/error
 */

import { create } from 'zustand';

export type ToastIntent = 'info' | 'success' | 'warning' | 'error';

export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface Toast {
  id: string;
  intent: ToastIntent;
  message: string;
  title?: string;
  sticky?: boolean;     // if true, no auto-dismiss
  duration?: number;    // ms override
  action?: ToastAction; // optional CTA button
  createdAt: number;    // ms epoch
}

export interface ToastsConfig {
  maxItems?: number; // default 5
  defaultDuration?: number; // ms fallback when intent-specific not set (default 4000)
  durations?: Partial<Record<ToastIntent, number>>; // intent-specific defaults
}

export interface ToastsState {
  items: Toast[];
  maxItems: number;
  defaultDuration: number;
  durations: Record<ToastIntent, number>;

  configure: (cfg: ToastsConfig) => void;

  enqueue: (input: Omit<Toast, 'id' | 'createdAt'> & { id?: string }) => string;
  dismiss: (id: string) => void;
  clear: () => void;

  // convenience helpers
  info: (message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) => string;
  success: (message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) => string;
  warning: (message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) => string;
  error: (message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) => string;
}

// ------------------------------ Internals -----------------------------------

// Local map for auto-dismiss timers. Kept outside Zustand state to avoid
// serialization issues and unintentional re-renders.
const timers = new Map<string, ReturnType<typeof setTimeout>>();

function genId(): string {
  const uuid = (globalThis as any)?.crypto?.randomUUID?.();
  if (uuid) return uuid;
  // Fallback that is good-enough for UI purposes
  return `t_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

const DEFAULT_MAX_ITEMS = 5;
const DEFAULT_DURATION = 4000; // ms
const DEFAULT_INTENT_DURATIONS: Record<ToastIntent, number> = {
  info: 4000,
  success: 3000,
  warning: 7000,
  error: 9000,
};

function computeDuration(
  intent: ToastIntent,
  sticky: boolean | undefined,
  explicit: number | undefined,
  cfg: { defaultDuration: number; durations: Record<ToastIntent, number> }
): number | undefined {
  if (sticky) return undefined;
  if (Number.isFinite(explicit as number)) return explicit as number;
  return cfg.durations[intent] ?? cfg.defaultDuration;
}

function scheduleAutoDismiss(id: string, ms?: number) {
  if (!ms || ms <= 0) return;
  // Clear any existing timer first
  const prev = timers.get(id);
  if (prev) clearTimeout(prev);
  const handle = setTimeout(() => {
    try {
      useToastsStore.getState().dismiss(id);
    } catch {
      // ignore
    }
  }, ms);
  timers.set(id, handle);
}

// ------------------------------- Store --------------------------------------

export const useToastsStore = create<ToastsState>((set, get) => ({
  items: [],
  maxItems: DEFAULT_MAX_ITEMS,
  defaultDuration: DEFAULT_DURATION,
  durations: { ...DEFAULT_INTENT_DURATIONS },

  configure: (cfg) =>
    set((s) => ({
      maxItems: cfg.maxItems ?? s.maxItems,
      defaultDuration: cfg.defaultDuration ?? s.defaultDuration,
      durations: { ...s.durations, ...(cfg.durations ?? {}) },
    })),

  enqueue: (input) => {
    const id = input.id ?? genId();
    const createdAt = Date.now();

    const cfg = { defaultDuration: get().defaultDuration, durations: get().durations };
    const duration = computeDuration(input.intent, input.sticky, input.duration, cfg);

    // Build toast
    const toast: Toast = {
      id,
      intent: input.intent,
      message: input.message,
      title: input.title,
      sticky: input.sticky,
      duration,
      action: input.action,
      createdAt,
    };

    set((s) => {
      // If over capacity, drop the oldest (end of array)
      const next = [toast, ...s.items];
      if (next.length > s.maxItems) next.length = s.maxItems;
      return { items: next };
    });

    scheduleAutoDismiss(id, duration);
    return id;
  },

  dismiss: (id) => {
    const t = timers.get(id);
    if (t) {
      clearTimeout(t);
      timers.delete(id);
    }
    set((s) => ({ items: s.items.filter((x) => x.id !== id) }));
  },

  clear: () => {
    // Clear all timers
    for (const t of timers.values()) clearTimeout(t);
    timers.clear();
    set({ items: [] });
  },

  info: (message, opts) =>
    get().enqueue({ intent: 'info', message, title: opts?.title, sticky: opts?.sticky, duration: opts?.duration, action: opts?.action }),

  success: (message, opts) =>
    get().enqueue({ intent: 'success', message, title: opts?.title, sticky: opts?.sticky, duration: opts?.duration, action: opts?.action }),

  warning: (message, opts) =>
    get().enqueue({ intent: 'warning', message, title: opts?.title, sticky: opts?.sticky, duration: opts?.duration, action: opts?.action }),

  error: (message, opts) =>
    get().enqueue({ intent: 'error', message, title: opts?.title, sticky: opts?.sticky, duration: opts?.duration, action: opts?.action }),
}));

// ------------------------------ Facade API ----------------------------------

/**
 * Global convenience API for non-React call sites.
 * Example:
 *   import { toast } from '@/state/toasts';
 *   toast.success('Deployed!', { title: 'Contract', action: { label: 'View', onClick: () => ... }});
 */
export const toast = {
  configure(cfg: ToastsConfig) {
    useToastsStore.getState().configure(cfg);
  },
  info(message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) {
    return useToastsStore.getState().info(message, opts);
  },
  success(message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) {
    return useToastsStore.getState().success(message, opts);
  },
  warning(message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) {
    return useToastsStore.getState().warning(message, opts);
  },
  error(message: string, opts?: Partial<Omit<Toast, 'id' | 'intent' | 'message' | 'createdAt'>>) {
    return useToastsStore.getState().error(message, opts);
  },
  dismiss(id: string) {
    useToastsStore.getState().dismiss(id);
  },
  clear() {
    useToastsStore.getState().clear();
  },
};

export type { Toast as ToastItem };
