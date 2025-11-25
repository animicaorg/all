/**
 * Simple analytics helper (opt-in): Plausible or PostHog.
 * - Safe on SSR (no-ops on server)
 * - Respects Do Not Track (configurable; defaults to true)
 * - Lazy-loads providers only when enabled
 * - Exposes track(), pageview(), identify(), setEnabled()
 */

type Provider = 'plausible' | 'posthog';

export interface AnalyticsConfig {
  /** Master switch; if undefined, will read localStorage ('analytics:enabled'), default false. */
  enabled?: boolean;
  /** Which provider to use. */
  provider?: Provider;
  /** Respect Do Not Track header/setting (default true). */
  respectDNT?: boolean;
  /** -------- Plausible options -------- */
  plausibleDomain?: string;     // e.g. "animica.xyz" (required for Plausible)
  plausibleSrc?: string;        // override script src (self-host) e.g. "https://plausible.animica.xyz/js/script.js"
  /** -------- PostHog options -------- */
  posthogKey?: string;          // project API key
  posthogHost?: string;         // default "https://eu.posthog.com" or "https://us.posthog.com" (you choose)
  posthogAutocapture?: boolean; // default true
  debug?: boolean;              // console.debug logs
}

const LS_KEY = 'analytics:enabled';
const isBrowser = typeof window !== 'undefined' && typeof document !== 'undefined';

declare global {
  interface Window {
    plausible?: (event: string, opts?: { props?: Record<string, any] }) => void & { q?: any[] };
    posthog?: {
      init: (key: string, opts: Record<string, any>) => void;
      capture: (event: string, props?: Record<string, any>) => void;
      identify: (id: string, props?: Record<string, any>) => void;
      opt_in_capturing?: () => void;
      opt_out_capturing?: () => void;
      isFeatureEnabled?: (key: string) => boolean;
      onFeatureFlags?: (cb: () => void) => void;
      register?: (props: Record<string, any>) => void;
      reset?: () => void;
      __loaded?: boolean;
    };
    __ANIMICA_ANALYTICS_READY__?: boolean;
    __ANIMICA_ANALYTICS_PROVIDER__?: Provider;
  }
}

let currentConfig: AnalyticsConfig = {};
let readyPromise: Promise<void> | null = null;

/* --------------------------------- Public API -------------------------------- */

export async function initAnalytics(config: AnalyticsConfig = {}): Promise<void> {
  if (!isBrowser) return; // SSR no-op
  currentConfig = { respectDNT: true, posthogAutocapture: true, ...config };

  const enableState = resolveEnabled(config.enabled);
  setLocalEnabled(enableState);

  if (!enableState) {
    log('analytics disabled');
    tearDownProvider();
    return;
  }

  if (dntBlocked() && currentConfig.respectDNT !== false) {
    log('DNT respected; analytics disabled');
    tearDownProvider();
    return;
  }

  // Already initialized? (avoid double script loads during SPA route changes)
  if (window.__ANIMICA_ANALYTICS_READY__) {
    log('already initialized');
    return;
  }

  readyPromise = (async () => {
    if (currentConfig.provider === 'plausible') {
      await loadPlausible();
      window.__ANIMICA_ANALYTICS_PROVIDER__ = 'plausible';
      window.__ANIMICA_ANALYTICS_READY__ = true;
      log('plausible ready');
    } else if (currentConfig.provider === 'posthog') {
      await loadPosthog();
      window.__ANIMICA_ANALYTICS_PROVIDER__ = 'posthog';
      window.__ANIMICA_ANALYTICS_READY__ = true;
      log('posthog ready');
    } else {
      log('no provider configured');
    }
  })();

  return readyPromise;
}

/** Track a custom event with optional properties. Safe to call before ready (queued by providers). */
export function track(event: string, props?: Record<string, any>): void {
  if (!isEnabled()) return;
  const p = getProvider();
  if (p === 'plausible') {
    ensurePlausibleStub();
    window.plausible!(event, props ? { props } : undefined);
  } else if (p === 'posthog') {
    if (!window.posthog) return;
    window.posthog.capture(event, props);
  }
}

/** Record a pageview (useful for SPA route changes). */
export function pageview(path?: string, props?: Record<string, any>): void {
  if (!isEnabled()) return;
  const p = getProvider();
  if (p === 'plausible') {
    ensurePlausibleStub();
    // For SPA, Plausible recommends a manual pageview event
    // @ts-ignore
    window.plausible!('pageview', props ? { props } : undefined);
  } else if (p === 'posthog') {
    // PostHog tracks $pageview via autocapture; send explicit for SPAs too:
    window.posthog?.capture('$pageview', { $current_url: path ?? location.href, ...props });
  }
}

/** Identify the current user (PostHog only; Plausible intentionally anonymous). */
export function identify(id: string, props?: Record<string, any>): void {
  if (!isEnabled()) return;
  if (getProvider() === 'posthog') {
    window.posthog?.identify(id, props);
  }
}

/** User opt-in/out toggle persisted in localStorage. Automatically re-inits provider. */
export async function setEnabled(enabled: boolean): Promise<void> {
  if (!isBrowser) return;
  setLocalEnabled(enabled);
  if (!enabled) {
    tearDownProvider();
    log('disabled via setEnabled');
    return;
  }
  await initAnalytics(currentConfig);
}

/** Read current enabled flag (includes DNT check). */
export function isEnabled(): boolean {
  if (!isBrowser) return false;
  const local = getLocalEnabled();
  if (!local) return false;
  if (dntBlocked() && currentConfig.respectDNT !== false) return false;
  return true;
}

/** Return current provider, if initialized. */
export function getProvider(): Provider | undefined {
  return window.__ANIMICA_ANALYTICS_PROVIDER__ ?? currentConfig.provider;
}

/* ------------------------------- Providers --------------------------------- */

async function loadPlausible(): Promise<void> {
  const domain = currentConfig.plausibleDomain;
  const src = currentConfig.plausibleSrc ?? 'https://plausible.io/js/script.js';
  if (!domain) {
    console.warn('[analytics] Plausible requires plausibleDomain');
    return;
  }
  ensurePlausibleStub();
  await injectScript(src, {
    defer: true,
    attrs: { 'data-domain': domain, 'data-api': undefined },
  });
}

function ensurePlausibleStub() {
  if (!window.plausible) {
    const q: any[] = [];
    const fn = function (this: any, ...args: any[]) {
      (fn as any).q!.push(args);
    } as unknown as typeof window.plausible;
    (fn as any).q = q;
    window.plausible = fn;
  }
}

async function loadPosthog(): Promise<void> {
  const key = currentConfig.posthogKey;
  const host = currentConfig.posthogHost ?? 'https://us.posthog.com';

  if (!key) {
    console.warn('[analytics] PostHog requires posthogKey');
    return;
  }

  // Inject script if not present
  await injectScript('https://cdn.posthog.com/posthog.js', { defer: true });

  // Initialize
  window.posthog?.init(key, {
    api_host: host,
    autocapture: currentConfig.posthogAutocapture !== false,
    capture_pageview: false, // we'll call explicitly
    loaded: () => {
      window.posthog!.__loaded = true;
      log('posthog loaded');
    },
  });
}

/* --------------------------------- Helpers --------------------------------- */

function resolveEnabled(cfgEnabled?: boolean): boolean {
  if (typeof cfgEnabled === 'boolean') return cfgEnabled;
  const ls = getLocalEnabled();
  return ls ?? false;
}

function getLocalEnabled(): boolean | null {
  if (!isBrowser) return null;
  const v = localStorage.getItem(LS_KEY);
  if (v === null) return null;
  return v === '1';
}

function setLocalEnabled(en: boolean): void {
  if (!isBrowser) return;
  localStorage.setItem(LS_KEY, en ? '1' : '0');
}

function dntBlocked(): boolean {
  if (!isBrowser) return false;
  // Standard and vendor-prefixed checks
  const dnt = (navigator as any).doNotTrack || (window as any).doNotTrack || (navigator as any).msDoNotTrack;
  return dnt === '1' || dnt === 'yes';
}

function log(...args: any[]) {
  if (currentConfig.debug) console.debug('[analytics]', ...args);
}

function tearDownProvider() {
  // We do not remove loaded scripts (idempotent & harmless), but we can opt-out PostHog
  if (window.posthog && typeof window.posthog.opt_out_capturing === 'function') {
    window.posthog.opt_out_capturing!();
  }
  window.__ANIMICA_ANALYTICS_READY__ = false;
  window.__ANIMICA_ANALYTICS_PROVIDER__ = undefined;
}

/** Inject a <script> tag and await its load. Idempotent by src URL when possible. */
function injectScript(src: string, opts?: { defer?: boolean; async?: boolean; attrs?: Record<string, string | undefined> }): Promise<void> {
  return new Promise((resolve, reject) => {
    if (!isBrowser) return resolve();

    // Try to find existing
    const existing = Array.from(document.getElementsByTagName('script')).find(s => s.src === src);
    if (existing && (existing as any)._loaded) return resolve();
    if (existing) {
      existing.addEventListener('load', () => resolve(), { once: true });
      existing.addEventListener('error', () => reject(new Error(`Failed to load ${src}`)), { once: true });
      return;
    }

    const s = document.createElement('script');
    s.src = src;
    if (opts?.defer) s.defer = true;
    if (opts?.async) s.async = true;
    if (opts?.attrs) {
      for (const [k, v] of Object.entries(opts.attrs)) {
        if (v === undefined) continue;
        s.setAttribute(k, v);
      }
    }
    (s as any)._loaded = false;
    s.onload = () => {
      (s as any)._loaded = true;
      resolve();
    };
    s.onerror = () => reject(new Error(`Failed to load ${src}`));
    document.head.appendChild(s);
  });
}

/* --------------------------------- Default --------------------------------- */

export default {
  initAnalytics,
  track,
  pageview,
  identify,
  setEnabled,
  isEnabled,
  getProvider,
};
