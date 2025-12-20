import React, { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BrowserRouter, NavLink } from "react-router-dom";

// Router (will be provided in explorer-web/src/router.tsx)
import AppRouter from "./router";
import { ExplorerStoreProvider } from "./state/store";
import { useNetworkManager } from "./state/network";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { installGlobalErrorHandlers } from "./utils/errorHandler";
import { resolveRpcUrl } from "./config/rpcUrl";

// ────────────────────────────────────────────────────────────────────────────────
// Global event channels so other modules can toggle loader / push toasts without
// tight coupling. You can either import the helpers below or dispatch events:
//
//   window.dispatchEvent(new CustomEvent("explorer:toast", { detail: { message: "ok" }}))
//   window.dispatchEvent(new CustomEvent("explorer:loader", { detail: { on: true, label: "Fetching" }}))
//
// ────────────────────────────────────────────────────────────────────────────────
export type ToastKind = "info" | "success" | "warning" | "error";
export type ToastPayload = {
  id?: string;
  title?: string;
  message: string;
  kind?: ToastKind;
  durationMs?: number; // default 4500
};
export const emitToast = (payload: ToastPayload) =>
  window.dispatchEvent(new CustomEvent("explorer:toast", { detail: payload }));

export const setGlobalLoading = (on: boolean, label?: string) =>
  window.dispatchEvent(new CustomEvent("explorer:loader", { detail: { on, label } }));

// ────────────────────────────────────────────────────────────────────────────────
// App Shell
// ────────────────────────────────────────────────────────────────────────────────
export default function App() {
  const basename = (import.meta as any).env?.VITE_BASE_PATH || undefined;

  // Install global error handlers once
  useEffect(() => {
    installGlobalErrorHandlers();
  }, []);

  return (
    <ErrorBoundary>
      <ExplorerStoreProvider>
        <BrowserRouter basename={basename} future={{ v7_relativeSplatPath: true, v7_startTransition: true }}>
          <NetworkInitializer />
          <div className="app-root">
            <TopBar />
            <TopProgressBar />
            <div className="app-container">
              <SideNav />
              <main className="app-main" role="main" aria-live="polite">
                <ErrorBoundary>
                  <Suspense fallback={<RouteFallback />}>
                    <AppRouter />
                  </Suspense>
                </ErrorBoundary>
              </main>
            </div>

            <GlobalLoaderOverlay />
            <ToastHost />
            <Footer />
            <style>{globalCss}</style>
          </div>
        </BrowserRouter>
      </ExplorerStoreProvider>
    </ErrorBoundary>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Network Initializer - establishes RPC connection and manages network state
// ────────────────────────────────────────────────────────────────────────────────
function NetworkInitializer() {
  const { status, error, rpcUrl, expectedChainId } = useNetworkManager();
  
  useEffect(() => {
    if (status === 'connected') {
      console.log('[App] Network connected successfully');
      console.log('[App] RPC URL:', rpcUrl);
      console.log('[App] Chain ID:', expectedChainId);
    } else if (status === 'error') {
      console.error('[App] Network connection error:', error);
      console.error('[App] RPC URL:', rpcUrl);
      console.error('[App] Expected Chain ID:', expectedChainId);
    } else if (status === 'connecting') {
      console.log('[App] Connecting to network...');
    }
  }, [status, error, rpcUrl, expectedChainId]);
  
  return null; // This component only handles side effects
}

// ────────────────────────────────────────────────────────────────────────────────
// Top Bar with theme toggle and small network readout
// ────────────────────────────────────────────────────────────────────────────────
function TopBar() {
  const [theme, setTheme] = useTheme();
  const { status, rpcUrl, expectedChainId } = useNetworkManager();
  const configuredRpcUrl = resolveRpcUrl();
  
  const toggle = useCallback(() => setTheme(theme === "dark" ? "light" : "dark"), [theme, setTheme]);

  const statusClass = status === 'connected' ? 'status-connected' : status === 'connecting' ? 'status-connecting' : 'status-disconnected';
  const statusLabel = status === 'connected' ? 'Connected' : status === 'connecting' ? 'Connecting...' : 'Disconnected';
  const chainId = expectedChainId || "Unknown";

  return (
    <header className="topbar">
      <div className="brand">
        <svg viewBox="0 0 24 24" className="logo" aria-hidden>
          <circle cx="12" cy="12" r="10" />
          <path d="M6 12h12M12 6v12" />
        </svg>
        <div className="brand-text">
          <strong>Animica Explorer</strong>
          <span className="muted">PoIES • AICF • DA • Beacon</span>
        </div>
      </div>

      <div className="grow" />

      <div className="net-pill" title={`${statusLabel}\nRPC: ${rpcUrl || configuredRpcUrl}\nChain ID: ${chainId}`}>
        <span className={`dot ${statusClass}`} />
        <span className="label">Chain</span>
        <span className="value">{chainId}</span>
      </div>

      <button className="btn ghost" onClick={toggle} aria-label="Toggle theme">
        {theme === "dark" ? "🌙" : "☀️"}
      </button>
    </header>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Side Navigation
// ────────────────────────────────────────────────────────────────────────────────
function SideNav() {
  const nav = useMemo(
    () => [
      { to: "/", label: "Home", end: true },
      { to: "/blocks", label: "Blocks" },
      { to: "/tx", label: "Transactions" },
      { to: "/address", label: "Addresses" },
      { to: "/contracts", label: "Contracts" },
      { to: "/aicf", label: "AICF" },
      { to: "/da", label: "Data Availability" },
      { to: "/beacon", label: "Randomness" },
      { to: "/network", label: "Network" },
    ],
    []
  );

  return (
    <nav className="sidenav" aria-label="Primary">
      {nav.map((n) => (
        <NavLink
          key={n.to}
          to={n.to}
          end={Boolean(n.end)}
          className={({ isActive }) => "navlink" + (isActive ? " active" : "")}
        >
          {n.label}
        </NavLink>
      ))}
    </nav>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Route Suspense Fallback
// ────────────────────────────────────────────────────────────────────────────────
function RouteFallback() {
  useEffect(() => {
    setGlobalLoading(true, "Loading…");
    // auto-clear after short delay in case the route resolved but fallback persisted
    const t = setTimeout(() => setGlobalLoading(false), 600);
    return () => {
      clearTimeout(t);
      setGlobalLoading(false);
    };
  }, []);
  return null;
}

// ────────────────────────────────────────────────────────────────────────────────
/** Very light top progress bar that animates on route transitions or loader events. */
function TopProgressBar() {
  const [active, setActive] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    function onLoader(e: Event) {
      const detail = (e as CustomEvent).detail as { on?: boolean };
      if (detail?.on) start();
      else stop();
    }
    window.addEventListener("explorer:loader", onLoader);
    return () => window.removeEventListener("explorer:loader", onLoader);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = () => {
    if (!active) setActive(true);
    if (timer.current) window.clearTimeout(timer.current);
  };
  const stop = () => {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setActive(false), 250);
  };

  return <div className={"top-progress" + (active ? " active" : "")} aria-hidden="true" />;
}

// ────────────────────────────────────────────────────────────────────────────────
// Global Loader Overlay controlled via window "explorer:loader" events
// ────────────────────────────────────────────────────────────────────────────────
function GlobalLoaderOverlay() {
  const [visible, setVisible] = useState(false);
  const [label, setLabel] = useState<string | undefined>();
  const pendingCount = useRef(0);

  useEffect(() => {
    function onLoader(e: Event) {
      const detail = (e as CustomEvent).detail as { on?: boolean; label?: string };
      if (detail?.on) {
        pendingCount.current += 1;
        setLabel(detail.label ?? "Loading…");
        setVisible(true);
      } else {
        pendingCount.current = Math.max(0, pendingCount.current - 1);
        if (pendingCount.current === 0) {
          setVisible(false);
          setLabel(undefined);
        }
      }
    }
    window.addEventListener("explorer:loader", onLoader);
    return () => window.removeEventListener("explorer:loader", onLoader);
  }, []);

  if (!visible) return null;
  return (
    <div className="loader-overlay" role="status" aria-live="polite" aria-label={label ?? "Loading"}>
      <div className="loader-card">
        <div className="spinner" aria-hidden />
        <div className="loader-text">
          <strong>{label ?? "Loading…"}</strong>
          <span className="muted">Please wait...</span>
        </div>
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Toast Host controlled via window "explorer:toast" events
// ────────────────────────────────────────────────────────────────────────────────
type Toast = Required<Pick<ToastPayload, "message">> &
  Pick<ToastPayload, "id" | "title" | "kind"> & { expiresAt: number };

function ToastHost() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    function onToast(e: Event) {
      const { id, title, message, kind, durationMs }: ToastPayload = (e as CustomEvent).detail;
      const expiresAt = Date.now() + (durationMs ?? 4500);
      const resolvedKind = kind ?? "info";
      const resolvedTitle = resolvedKind === "error" ? title ?? "Error" : title;
      const t: Toast = {
        id: id ?? cryptoRandomId(),
        title: resolvedTitle,
        message,
        kind: resolvedKind,
        expiresAt,
      };
      setToasts((prev) => [...prev.filter((x) => x.id !== t.id), t]);
    }
    window.addEventListener("explorer:toast", onToast);
    return () => window.removeEventListener("explorer:toast", onToast);
  }, []);

  // GC expired toasts
  useEffect(() => {
    if (toasts.length === 0) return;
    const iv = window.setInterval(() => {
      const now = Date.now();
      setToasts((prev) => prev.filter((t) => t.expiresAt > now));
    }, 500);
    return () => window.clearInterval(iv);
  }, [toasts.length]);

  return (
    <div className="toast-host" aria-live="polite" aria-relevant="additions">
      {toasts.map((t) => (
        <div key={t.id} className={"toast " + (t.kind ?? "info")}>
          {t.title && <div className="toast-title">{t.title}</div>}
          <div className="toast-msg">{t.message}</div>
          <button className="toast-close" onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))} aria-label="Dismiss">
            ×
          </button>
        </div>
      ))}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Footer
// ────────────────────────────────────────────────────────────────────────────────
function Footer() {
  return (
    <footer className="footer">
      <span className="muted">© {new Date().getFullYear()} Animica • Explorer</span>
      <div className="grow" />
      <CacheStatusWrapper />
      <a
        className="muted link"
        href="https://github.com/animica-labs"
        target="_blank"
        rel="noreferrer"
        aria-label="GitHub"
      >
        GitHub
      </a>
    </footer>
  );
}

// Simple wrapper for cache status in footer
function CacheStatusWrapper() {
  const [cacheInfo, setCacheInfo] = useState<{ synced: boolean; blocksCount: number } | null>(null);
  const [showDetails, setShowDetails] = useState(false);

  useEffect(() => {
    // Check cache availability and get basic stats
    async function checkCache() {
      try {
        const { isCacheAvailable, getCache } = await import('./services/cache');
        if (!isCacheAvailable()) return;
        
        const cache = await getCache();
        const stats = await cache.getStats();
        setCacheInfo({
          synced: (stats.lastSyncHeight ?? 0) > 0,
          blocksCount: stats.blocksCount,
        });
      } catch (err) {
        // Cache not available or error - silently ignore
        console.debug('[Footer] Cache check failed:', err);
      }
    }
    
    checkCache().catch((err) => {
      console.debug('[Footer] Unexpected error in checkCache:', err);
    });
    
    // Refresh every 30 seconds
    const interval = setInterval(() => {
      checkCache().catch((err) => {
        console.debug('[Footer] Unexpected error in checkCache interval:', err);
      });
    }, 30000);
    
    return () => clearInterval(interval);
  }, []);

  if (!cacheInfo) return null;

  return (
    <div className="cache-footer-pill" title={`Local cache: ${cacheInfo.blocksCount.toLocaleString()} blocks cached`}>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
        <line x1="3" y1="9" x2="21" y2="9"/>
        <line x1="9" y1="21" x2="9" y2="9"/>
      </svg>
      <span className="muted" style={{ fontSize: '0.85rem' }}>
        {cacheInfo.synced ? '💾' : '⏳'} {cacheInfo.blocksCount.toLocaleString()}
      </span>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────────
// Hooks & Utils
// ────────────────────────────────────────────────────────────────────────────────
function useTheme(): ["light" | "dark", (t: "light" | "dark") => void] {
  const [theme, setThemeState] = useState<"light" | "dark">(() => {
    try {
      const key = "animica-theme";
      const stored = localStorage.getItem(key) as "light" | "dark" | null;
      if (stored) return stored;
      const prefersDark =
        typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
      return prefersDark ? "dark" : "light";
    } catch {
      return "light";
    }
  });

  const setTheme = useCallback((t: "light" | "dark") => {
    setThemeState(t);
    try {
      localStorage.setItem("animica-theme", t);
      document.documentElement.setAttribute("data-theme", t);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    // Ensure DOM reflects initial theme
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  return [theme, setTheme];
}

function cryptoRandomId() {
  try {
    const b = new Uint8Array(8);
    crypto.getRandomValues(b);
    return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
  } catch {
    return Math.random().toString(16).slice(2);
  }
}

// ────────────────────────────────────────────────────────────────────────────────
// Minimal CSS (scoped via CSS-in-TS to avoid external deps). This complements
// tokens.css/theme.css, but keeps App usable standalone.
// ────────────────────────────────────────────────────────────────────────────────
const globalCss = `
:root{
  --bg: #0b0c10;
  --panel:#111318;
  --text:#e8edf2;
  --muted:#a3adba;
  --accent:#40a9ff;
  --ok:#3ccf91;
  --warn:#f0a500;
  --err:#ff6b6b;
  --border:#1e222b;
}
:root[data-theme="light"]{
  --bg:#ffffff;
  --panel:#f6f8fb;
  --text:#0b0c10;
  --muted:#4b5563;
  --accent:#2563eb;
  --ok:#059669;
  --warn:#b45309;
  --err:#dc2626;
  --border:#e5e7eb;
}
*{box-sizing:border-box}
html,body,#root,.app-root{height:100%}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.4 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,Noto Sans,sans-serif}
.muted{color:var(--muted)}
.link{color:var(--accent);text-decoration:none}
.link:hover{text-decoration:underline}

.topbar{
  display:flex;align-items:center;gap:.75rem;
  padding:.6rem .9rem;border-bottom:1px solid var(--border);background:var(--panel);position:sticky;top:0;z-index:50
}
.brand{display:flex;align-items:center;gap:.6rem}
.logo{width:24px;height:24px;fill:none;stroke:var(--accent);stroke-width:2}
.brand-text{display:flex;flex-direction:column}
.grow{flex:1}
.btn.ghost{background:transparent;border:1px solid var(--border);color:var(--text);padding:.35rem .6rem;border-radius:.4rem;cursor:pointer}
.btn.ghost:hover{border-color:var(--accent)}
.net-pill{display:flex;align-items:center;gap:.4rem;border:1px solid var(--border);padding:.3rem .6rem;border-radius:999px;background:var(--panel);transition:all .2s ease}
.net-pill:hover{border-color:var(--accent);box-shadow:0 0 0 3px rgba(64,169,255,.1)}
.net-pill .dot{width:8px;height:8px;border-radius:999px;background:var(--ok);animation:pulse 2s ease-in-out infinite}
.net-pill .dot.status-connected{background:var(--ok);box-shadow:0 0 0 2px rgba(60,207,145,.2);animation:none}
.net-pill .dot.status-connecting{background:var(--warn);box-shadow:0 0 0 2px rgba(240,165,0,.2)}
.net-pill .dot.status-disconnected{background:var(--err);box-shadow:0 0 0 2px rgba(255,107,107,.2);animation:none}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
.net-pill .label{opacity:.8;font-size:.85rem}
.net-pill .value{font-weight:600;font-size:.9rem}

.app-container{display:grid;grid-template-columns: 220px 1fr; height: calc(100% - 52px)}
.sidenav{border-right:1px solid var(--border);padding:.6rem;background:linear-gradient(180deg,var(--panel),transparent)}
.navlink{display:block;padding:.5rem .6rem;border-radius:.4rem;color:var(--text);text-decoration:none}
.navlink:hover{background:rgba(128,128,128,.08)}
.navlink.active{background:rgba(128,128,128,.14);color:var(--accent);font-weight:600}

.app-main{padding:1rem;min-width:0}

.footer{display:flex;gap:.8rem;align-items:center;justify-content:space-between;border-top:1px solid var(--border);padding:.7rem .9rem;background:var(--panel)}
.cache-footer-pill{display:flex;align-items:center;gap:.3rem;padding:.25rem .5rem;border-radius:.3rem;background:rgba(128,128,128,.08);border:1px solid var(--border)}

.top-progress{position:fixed;inset:0 0 auto 0;height:2px;background:linear-gradient(90deg,var(--accent),transparent);transform:scaleX(0);transform-origin:left;transition:transform .2s ease;z-index:60;opacity:.8}
.top-progress.active{transform:scaleX(1);animation:tp 1.1s ease-in-out infinite}
@keyframes tp{0%{opacity:.4}50%{opacity:1}100%{opacity:.4}}

.loader-overlay{position:fixed;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;z-index:70;backdrop-filter:saturate(120%) blur(8px);animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
.loader-card{display:flex;gap:1rem;align-items:center;background:var(--panel);border:1px solid var(--border);padding:1.5rem 2rem;border-radius:.8rem;box-shadow:0 20px 60px rgba(0,0,0,.35);animation:slideUp .3s ease}
@keyframes slideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.spinner{width:24px;height:24px;border:3px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
.loader-text{display:flex;flex-direction:column;gap:.2rem}
.loader-text strong{font-size:1rem;font-weight:600}

.toast-host{position:fixed;inset:auto 12px 12px auto;display:flex;flex-direction:column;gap:.5rem;z-index:80}
.toast{position:relative;max-width:380px;background:var(--panel);border:1px solid var(--border);padding:.7rem .9rem;border-radius:.6rem;box-shadow:0 10px 30px rgba(0,0,0,.25)}
.toast.info{border-color:var(--accent)}
.toast.success{border-color:var(--ok)}
.toast.warning{border-color:var(--warn)}
.toast.error{border-color:var(--err)}
.toast-title{font-weight:700;margin-bottom:.2rem}
.toast-msg{white-space:pre-wrap}
.toast-close{position:absolute;right:.35rem;top:.2rem;border:none;background:transparent;color:var(--muted);font-size:16px;cursor:pointer}
.toast-close:hover{color:var(--text)}

@media (max-width: 920px){
  .app-container{grid-template-columns: 1fr}
  .sidenav{display:flex;overflow-x:auto;overflow-y:hidden;gap:.4rem;border-right:none;border-bottom:1px solid var(--border);-webkit-overflow-scrolling:touch;padding:.5rem}
  .navlink{white-space:nowrap;min-height:44px;display:flex;align-items:center}
}

@media (max-width: 640px){
  .topbar{padding:.5rem .7rem;gap:.5rem}
  .brand-text{font-size:.9rem}
  .net-pill{font-size:.75rem;padding:.15rem .4rem}
  .app-main{padding:.75rem}
  .toast-host{inset:auto 8px 8px auto;max-width:calc(100vw - 16px)}
  .toast{max-width:100%}
  .footer{padding:.5rem .7rem;font-size:.8rem}
}
`;
