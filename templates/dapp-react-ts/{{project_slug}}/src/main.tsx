import React, { useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

/**
 * Entry for the Animica React + TS dApp template.
 *
 * Goals:
 *  - Detect the Animica wallet extension (window.animica, AIP-1193-like).
 *  - Allow user to connect, show selected account & chain.
 *  - Live-display the chain head (via provider events if present; otherwise JSON-RPC poll).
 *  - Provide a tiny, typed JSON-RPC helper as a fallback.
 *
 * This file intentionally has no styling framework dependency—just minimal inline CSS.
 */

/* ----------------------------- Global typings ------------------------------ */

declare global {
  interface AnimicaProvider {
    /** AIP-1193-like request method */
    request<T = unknown>(args: { method: string; params?: unknown[] | object }): Promise<T>;

    /** Event API (AIP-1193-like), e.g. 'accountsChanged', 'chainChanged', 'newHeads' */
    on?(event: string, listener: (...args: any[]) => void): void;
    removeListener?(event: string, listener: (...args: any[]) => void): void;
  }

  interface Window {
    animica?: AnimicaProvider;
  }
}

/* --------------------------------- Config --------------------------------- */

const RPC_URL = (import.meta as any).env?.VITE_RPC_URL ?? "http://localhost:8545/rpc";
const DEFAULT_CHAIN_ID: number = Number((import.meta as any).env?.VITE_CHAIN_ID ?? "1337");

/* ------------------------------ RPC utilities ------------------------------ */

type JsonRpcResponse<T = unknown> =
  | { jsonrpc: "2.0"; id: number | string | null; result: T }
  | { jsonrpc: "2.0"; id: number | string | null; error: { code: number; message: string; data?: unknown } };

async function httpRpc<T = unknown>(method: string, params?: unknown): Promise<T> {
  const body = {
    jsonrpc: "2.0",
    id: Math.floor(Math.random() * 1e9),
    method,
    params: params ?? [],
  };

  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`RPC HTTP ${res.status}: ${text || res.statusText}`);
  }

  const json = (await res.json()) as JsonRpcResponse<T>;
  if ("error" in json) throw new Error(`RPC ${method} failed: ${json.error.message} (${json.error.code})`);
  return json.result as T;
}

/* ------------------------------ UI Components ------------------------------ */

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        fontSize: 12,
        gap: 6,
        padding: "4px 10px",
        borderRadius: 999,
        border: "1px solid color-mix(in srgb, var(--fg) 20%, transparent)",
        background: "color-mix(in srgb, var(--fg) 8%, transparent)",
      }}
    >
      {children}
    </span>
  );
}

function Button(props: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      {...props}
      style={{
        cursor: "pointer",
        padding: ".65rem 1rem",
        borderRadius: 12,
        border: "1px solid color-mix(in srgb, var(--accent) 35%, transparent)",
        background: "color-mix(in srgb, var(--accent) 12%, transparent)",
        color: "var(--fg)",
        fontWeight: 600,
      }}
    />
  );
}

/* --------------------------------- <App /> -------------------------------- */

type HeadView = {
  number: number;
  hash: string;
  timestamp?: number;
};

const App: React.FC = () => {
  const provider = useMemo(() => window.animica, []);
  const [hasProvider, setHasProvider] = useState<boolean>(!!provider);

  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);

  const [head, setHead] = useState<HeadView | null>(null);
  const [status, setStatus] = useState<string>("idle");

  const headPoller = useRef<number | null>(null);

  // Detect provider late-injection (some extensions inject after DOM ready)
  useEffect(() => {
    if (hasProvider) return;
    const t = setInterval(() => {
      if (window.animica) {
        setHasProvider(true);
        clearInterval(t);
      }
    }, 300);
    return () => clearInterval(t);
  }, [hasProvider]);

  // Subscribe to provider events (accounts/chain/newHeads)
  useEffect(() => {
    if (!window.animica || !window.animica.on) return;

    const onAccounts = (accounts: string[]) => setAccount(accounts?.[0] ?? null);
    const onChain = (cid: number | string) => setChainId(typeof cid === "string" ? Number(cid) : (cid as number));
    const onNewHead = (h: HeadView) => setHead(h);

    window.animica.on("accountsChanged", onAccounts);
    window.animica.on("chainChanged", onChain);
    window.animica.on("newHeads", onNewHead);

    return () => {
      window.animica?.removeListener?.("accountsChanged", onAccounts);
      window.animica?.removeListener?.("chainChanged", onChain);
      window.animica?.removeListener?.("newHeads", onNewHead);
    };
  }, [hasProvider]);

  // Fallback polling for head if provider isn't available or doesn't emit newHeads
  useEffect(() => {
    if (headPoller.current) window.clearInterval(headPoller.current);
    headPoller.current = window.setInterval(async () => {
      try {
        const h = await httpRpc<HeadView>("chain.getHead");
        setHead(h);
      } catch {
        /* ignore transient errors */
      }
    }, 2500);
    return () => {
      if (headPoller.current) window.clearInterval(headPoller.current);
      headPoller.current = null;
    };
  }, []);

  async function connect() {
    try {
      if (!window.animica) throw new Error("Animica provider not found");
      setStatus("connecting");
      // Request accounts (AIP-1193-like; method name provided by wallet-extension)
      const accounts = await window.animica.request<string[]>({ method: "animica_requestAccounts" });
      setAccount(accounts?.[0] ?? null);

      // Query chainId (number)
      const cid = await window.animica.request<number>({ method: "animica_chainId" });
      setChainId(cid ?? DEFAULT_CHAIN_ID);
      setStatus("connected");
    } catch (err: any) {
      setStatus(`error: ${err?.message ?? String(err)}`);
    }
  }

  async function ensureChain(target: number = DEFAULT_CHAIN_ID) {
    if (!window.animica) return;
    try {
      const current = await window.animica.request<number>({ method: "animica_chainId" });
      if (Number(current) !== Number(target)) {
        // Optional: ask wallet to switch network if it supports it.
        // This is a hint; some wallets may not expose this method.
        await window.animica.request({ method: "animica_switchChain", params: [{ chainId: target }] });
        setChainId(target);
      }
    } catch {
      /* noop */
    }
  }

  return (
    <div style={{ padding: "2rem 1.25rem", maxWidth: 880, margin: "0 auto" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 18 }}>
        <h1 style={{ margin: 0, fontSize: "clamp(1.25rem, 1.1rem + 1.4vw, 1.9rem)" }}>
          {{project_name}} <span style={{ opacity: 0.5, fontWeight: 400 }}>• Animica dApp</span>
        </h1>

        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Badge>RPC: <code>{RPC_URL}</code></Badge>
          <Badge>Chain: <code>{chainId ?? "—"}</code></Badge>
          <Badge>Acct: <code>{account ? shortAddr(account) : "—"}</code></Badge>
        </div>
      </header>

      <section
        style={{
          border: "1px solid color-mix(in srgb, var(--fg) 15%, transparent)",
          background: "color-mix(in srgb, var(--fg) 6%, transparent)",
          borderRadius: 14,
          padding: "1rem",
          marginBottom: "1.25rem",
        }}
      >
        <h2 style={{ marginTop: 0 }}>Wallet</h2>
        {hasProvider ? (
          <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <Button onClick={connect} disabled={status === "connecting"}>
              {status === "connecting" ? "Connecting…" : "Connect"}
            </Button>
            <Button onClick={() => ensureChain()} disabled={!account}>
              Ensure Chain ({DEFAULT_CHAIN_ID})
            </Button>
            <span style={{ color: "var(--muted)" }}>
              {status === "idle" ? "Not connected" : status}
            </span>
          </div>
        ) : (
          <div>
            <p style={{ marginBottom: 8 }}>
              No Animica wallet detected. Install the browser extension and refresh.
            </p>
            <ul style={{ marginTop: 0, paddingLeft: "1.25rem", lineHeight: 1.6 }}>
              <li>After installation, this page will detect <code>window.animica</code>.</li>
              <li>You can still read the head below via JSON-RPC (no signing).</li>
            </ul>
          </div>
        )}
      </section>

      <section
        style={{
          border: "1px solid color-mix(in srgb, var(--fg) 15%, transparent)",
          background: "color-mix(in srgb, var(--fg) 6%, transparent)",
          borderRadius: 14,
          padding: "1rem",
        }}
      >
        <h2 style={{ marginTop: 0 }}>Chain Head</h2>
        {head ? (
          <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", gap: 8 }}>
            <div style={{ color: "var(--muted)" }}>Height</div>
            <div><code>{head.number}</code></div>
            <div style={{ color: "var(--muted)" }}>Hash</div>
            <div><code>{head.hash}</code></div>
            {head.timestamp != null && (
              <>
                <div style={{ color: "var(--muted)" }}>Timestamp</div>
                <div><code>{new Date(head.timestamp * 1000).toISOString()}</code></div>
              </>
            )}
          </div>
        ) : (
          <p style={{ color: "var(--muted)" }}>Fetching latest head…</p>
        )}
      </section>

      <footer style={{ marginTop: 18, color: "var(--muted)", fontSize: 14 }}>
        <p style={{ marginTop: 0 }}>
          Tip: this template supports both provider-driven updates (<code>newHeads</code>)
          and a fallback JSON-RPC poll to <code>chain.getHead</code>.
        </p>
      </footer>
    </div>
  );
};

/* --------------------------------- Helpers -------------------------------- */

function shortAddr(addr: string, size: number = 6) {
  if (!addr) return "";
  if (addr.length <= size * 2 + 3) return addr;
  return `${addr.slice(0, size)}…${addr.slice(-size)}`;
}

/* --------------------------------- Render --------------------------------- */

const rootEl = document.getElementById("root");
if (!rootEl) {
  const msg =
    "Missing #root element. Ensure your project root index.html has <div id=\"root\"></div> and includes /src/main.tsx.";
  throw new Error(msg);
}
createRoot(rootEl).render(<App />);

/* --------------------------------- Styles --------------------------------- */
/* These vars mirror the palette used in templates/dapp-react-ts/{{project_slug}}/public/index.html */
const style = document.createElement("style");
style.innerHTML = `
:root {
  --bg: #0d1117;
  --muted: #8b949e;
  --fg: #c9d1d9;
  --accent: #58a6ff;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #ffffff;
    --muted: #6b7280;
    --fg: #111827;
    --accent: #2563eb;
  }
}
html, body {
  background: var(--bg);
  color: var(--fg);
  margin: 0;
  font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial,
    Apple Color Emoji, Segoe UI Emoji;
}
a { color: var(--accent); }
`;
document.head.appendChild(style);
