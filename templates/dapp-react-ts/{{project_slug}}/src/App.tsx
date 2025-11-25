import React, { useEffect, useMemo, useRef, useState } from "react";

/**
 * App.tsx — primary UI for the Animica React + TS dApp template.
 *
 * This component focuses on:
 *  - Detecting the Animica wallet provider (window.animica).
 *  - Connecting an account and reading chain/network information.
 *  - Displaying the live chain head (via provider events or JSON-RPC polling).
 *  - Reading basic account state (balance/nonce) through JSON-RPC.
 *
 * Notes:
 *  - The wallet extension is AIP-1193-like. Method names used here
 *    (animica_requestAccounts, animica_chainId, newHeads, etc.) match the
 *    extension plan described in the repo.
 *  - JSON-RPC fallback uses POST to VITE_RPC_URL (defaults to localhost).
 *  - No styling framework is required. Minimal inline styles + CSS vars
 *    defined in index.html/main.tsx keep things looking decent.
 */

/* ----------------------------- Global typings ------------------------------ */

declare global {
  interface AnimicaProvider {
    /** AIP-1193-like request method (promisified) */
    request<T = unknown>(args: { method: string; params?: unknown[] | object }): Promise<T>;

    /** Optional event API; wallet may emit 'accountsChanged' | 'chainChanged' | 'newHeads' */
    on?(event: string, listener: (...args: any[]) => void): void;
    removeListener?(event: string, listener: (...args: any[]) => void): void;
  }

  interface Window {
    animica?: AnimicaProvider;
  }
}

/* --------------------------------- Config --------------------------------- */

const RPC_URL: string = (import.meta as any).env?.VITE_RPC_URL ?? "http://localhost:8545/rpc";
const DEFAULT_CHAIN_ID: number = Number((import.meta as any).env?.VITE_CHAIN_ID ?? "1337");

/* ------------------------------ RPC utilities ------------------------------ */

type JsonRpcResponse<T = unknown> =
  | { jsonrpc: "2.0"; id: number | string | null; result: T }
  | { jsonrpc: "2.0"; id: number | string | null; error: { code: number; message: string; data?: unknown } };

async function httpRpc<T = unknown>(method: string, params?: unknown): Promise<T> {
  const body = {
    jsonrpc: "2.0" as const,
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
  if ("error" in json) {
    throw new Error(`RPC ${method} failed: ${json.error.message} (${json.error.code})`);
  }
  return json.result as T;
}

/* ----------------------------- Domain typings ------------------------------ */

type HeadView = {
  number: number;
  hash: string;
  timestamp?: number;
};

type BalanceResult = string | number | { raw: string | number };

/* --------------------------------- Helpers -------------------------------- */

function shortAddr(addr: string, size = 6) {
  if (!addr) return "";
  return addr.length <= size * 2 + 3 ? addr : `${addr.slice(0, size)}…${addr.slice(-size)}`;
}

function toBigIntish(x: BalanceResult | null | undefined): bigint | null {
  if (x == null) return null;
  if (typeof x === "object" && x && "raw" in x) {
    const v = (x as any).raw;
    return typeof v === "string" ? BigInt(v) : BigInt(v ?? 0);
  }
  return typeof x === "string" ? BigInt(x) : BigInt(x);
}

/* ---------------------------------- App ----------------------------------- */

const App: React.FC = () => {
  /* Provider detection */
  const provider = useMemo(() => window.animica, []);
  const [hasProvider, setHasProvider] = useState<boolean>(!!provider);

  /* Session state */
  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number | null>(null);

  /* Chain head */
  const [head, setHead] = useState<HeadView | null>(null);

  /* Account state */
  const [balance, setBalance] = useState<bigint | null>(null);
  const [nonce, setNonce] = useState<number | null>(null);

  /* UI status */
  const [status, setStatus] = useState<string>("idle");
  const [rpcError, setRpcError] = useState<string | null>(null);

  /* Polling timer for head (fallback if provider doesn't emit 'newHeads') */
  const headPoller = useRef<number | null>(null);

  /* Detect late injection (extensions often inject after DOM is ready) */
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

  /* Subscribe to provider events if available */
  useEffect(() => {
    if (!window.animica || !window.animica.on) return;

    const onAccounts = (accounts: string[]) => setAccount(accounts?.[0] ?? null);
    const onChain = (cid: number | string) => setChainId(Number(cid));
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

  /* Fallback polling for head via JSON-RPC */
  useEffect(() => {
    if (headPoller.current) window.clearInterval(headPoller.current);
    headPoller.current = window.setInterval(async () => {
      try {
        const h = await httpRpc<HeadView>("chain.getHead");
        setHead(h);
        setRpcError(null);
      } catch (err: any) {
        setRpcError(err?.message ?? String(err));
      }
    }, 2500);

    return () => {
      if (headPoller.current) window.clearInterval(headPoller.current);
      headPoller.current = null;
    };
  }, []);

  /* Actions */

  async function connect() {
    try {
      if (!window.animica) throw new Error("Animica provider not found");
      setStatus("connecting…");
      const accounts = await window.animica.request<string[]>({ method: "animica_requestAccounts" });
      setAccount(accounts?.[0] ?? null);

      const cid = await window.animica.request<number>({ method: "animica_chainId" });
      setChainId(Number(cid));
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
        await window.animica.request({ method: "animica_switchChain", params: [{ chainId: target }] });
        setChainId(target);
      }
    } catch {
      /* switching is best-effort; ignore unsupported */
    }
  }

  async function refreshAccountData(addr?: string) {
    const a = addr ?? account;
    if (!a) return;
    try {
      setStatus("loading account…");
      // Named params supported by the server dispatcher (positional also fine).
      const [balRaw, nonceRaw] = await Promise.all([
        httpRpc<BalanceResult>("state.getBalance", { address: a }),
        httpRpc<number>("state.getNonce", { address: a }),
      ]);
      setBalance(toBigIntish(balRaw));
      setNonce(nonceRaw ?? null);
      setStatus("ready");
      setRpcError(null);
    } catch (err: any) {
      setStatus("ready");
      setRpcError(err?.message ?? String(err));
    }
  }

  /* ------------------------------- Rendering ------------------------------- */

  return (
    <div style={{ display: "grid", gap: 16 }}>
      {/* Wallet Section */}
      <section style={card}>
        <h2 style={h2}>Wallet</h2>
        {hasProvider ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center" }}>
            <Button onClick={connect} disabled={status === "connecting…"}>
              {status === "connecting…" ? "Connecting…" : "Connect"}
            </Button>
            <Button onClick={() => ensureChain()} disabled={!account}>
              Ensure Chain ({DEFAULT_CHAIN_ID})
            </Button>
            <Badge>
              Chain: <code>{chainId ?? "—"}</code>
            </Badge>
            <Badge>
              Account: <code>{account ? shortAddr(account) : "—"}</code>
            </Badge>
            <span style={{ color: "var(--muted)", marginLeft: 6 }}>{status}</span>
          </div>
        ) : (
          <div>
            <p style={{ marginBottom: 8 }}>
              No Animica wallet detected. Install the extension and refresh this page.
            </p>
            <ul style={{ marginTop: 0, paddingLeft: "1.25rem", lineHeight: 1.6 }}>
              <li>You can still read the chain head below via JSON-RPC.</li>
              <li>Provider events (e.g. <code>newHeads</code>) require the extension.</li>
            </ul>
          </div>
        )}
      </section>

      {/* Chain Head Section */}
      <section style={card}>
        <h2 style={h2}>Chain Head</h2>
        {head ? (
          <KvGrid
            rows={[
              ["Height", <code key="h">{head.number}</code>],
              ["Hash", <code key="hh">{head.hash}</code>],
              head.timestamp != null
                ? ["Timestamp", <code key="ts">{new Date(head.timestamp * 1000).toISOString()}</code>]
                : null,
            ].filter(Boolean) as [string, React.ReactNode][]}
          />
        ) : (
          <p style={{ color: "var(--muted)" }}>Fetching latest head…</p>
        )}
        {!!rpcError && (
          <p style={{ color: "tomato", marginTop: 10 }}>
            RPC error: <code>{rpcError}</code>
          </p>
        )}
      </section>

      {/* Account State (Balance & Nonce) */}
      <section style={card}>
        <h2 style={h2}>Account</h2>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Button onClick={() => refreshAccountData()} disabled={!account}>
            Refresh balance &amp; nonce
          </Button>
          <Badge>RPC: <code>{RPC_URL}</code></Badge>
        </div>

        {account ? (
          <div style={{ marginTop: 12 }}>
            <KvGrid
              rows={[
                ["Address", <code key="a">{account}</code>],
                ["Balance (raw units)", <code key="b">{balance != null ? balance.toString() : "—"}</code>],
                ["Nonce", <code key="n">{nonce ?? "—"}</code>],
              ]}
            />
            <p style={{ color: "var(--muted)" }}>
              Balances are returned in the chain’s smallest unit. Formatting into human units is
              app-specific and depends on token decimals (if applicable).
            </p>
          </div>
        ) : (
          <p style={{ color: "var(--muted)", marginTop: 10 }}>Connect a wallet to query account state.</p>
        )}
      </section>

      {/* Quick JSON-RPC Console (read-only helper) */}
      <section style={card}>
        <h2 style={h2}>Quick JSON-RPC (read-only)</h2>
        <p style={{ marginTop: 0, color: "var(--muted)" }}>
          Handy for experimenting with the node’s RPC surface. This performs a POST to{" "}
          <code>{RPC_URL}</code>.
        </p>
        <RpcPlayground />
      </section>
    </div>
  );
};

export default App;

/* ------------------------------ Small UI bits ------------------------------ */

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12,
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

const card: React.CSSProperties = {
  border: "1px solid color-mix(in srgb, var(--fg) 15%, transparent)",
  background: "color-mix(in srgb, var(--fg) 6%, transparent)",
  borderRadius: 14,
  padding: "1rem",
};

const h2: React.CSSProperties = { marginTop: 0 };

/* -------------------------------- Components ------------------------------- */

function KvGrid({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 8 }}>
      {rows.map(([k, v]) => (
        <React.Fragment key={k}>
          <div style={{ color: "var(--muted)" }}>{k}</div>
          <div>{v}</div>
        </React.Fragment>
      ))}
    </div>
  );
}

/**
 * Tiny JSON-RPC playground for read-only calls.
 * - Good targets: chain.getHead, state.getBalance, state.getNonce, chain.getParams, etc.
 * - Mutating calls (like tx submission) are intentionally omitted here.
 */
const RpcPlayground: React.FC = () => {
  const [method, setMethod] = useState<string>("chain.getHead");
  const [params, setParams] = useState<string>("{}");
  const [out, setOut] = useState<string>("");

  async function run() {
    setOut("…");
    try {
      const parsed = params.trim() ? JSON.parse(params) : undefined;
      const res = await httpRpc<any>(method, parsed);
      setOut(JSON.stringify(res, null, 2));
    } catch (err: any) {
      setOut(`Error: ${err?.message ?? String(err)}`);
    }
  }

  return (
    <div style={{ display: "grid", gap: 10 }}>
      <div style={{ display: "grid", gap: 8 }}>
        <label>
          <div style={{ marginBottom: 6, color: "var(--muted)" }}>Method</div>
          <input
            value={method}
            onChange={(e) => setMethod(e.target.value)}
            placeholder="chain.getHead"
            style={inputStyle}
          />
        </label>
        <label>
          <div style={{ marginBottom: 6, color: "var(--muted)" }}>Params (JSON; object or array)</div>
          <textarea
            rows={6}
            value={params}
            onChange={(e) => setParams(e.target.value)}
            placeholder="{}"
            style={{ ...inputStyle, fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace" }}
          />
        </label>
      </div>
      <div style={{ display: "flex", gap: 10 }}>
        <Button onClick={run}>Run</Button>
        <Button
          onClick={() => {
            setMethod("state.getBalance");
            setParams(JSON.stringify({ address: "anim1..." }, null, 2));
          }}
        >
          Example: state.getBalance
        </Button>
        <Button
          onClick={() => {
            setMethod("state.getNonce");
            setParams(JSON.stringify({ address: "anim1..." }, null, 2));
          }}
        >
          Example: state.getNonce
        </Button>
      </div>
      <div>
        <div style={{ marginBottom: 6, color: "var(--muted)" }}>Result</div>
        <pre
          style={{
            margin: 0,
            padding: "10px 12px",
            background: "color-mix(in srgb, var(--fg) 8%, transparent)",
            border: "1px solid color-mix(in srgb, var(--fg) 18%, transparent)",
            borderRadius: 10,
            overflowX: "auto",
          }}
        >
          {out || "—"}
        </pre>
      </div>
    </div>
  );
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "10px 12px",
  borderRadius: 10,
  border: "1px solid color-mix(in srgb, var(--fg) 18%, transparent)",
  background: "transparent",
  color: "var(--fg)",
};
