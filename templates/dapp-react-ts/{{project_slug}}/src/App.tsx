import React, { useEffect, useMemo, useState } from "react";
import {
  getAnimicaProvider,
  installAnimicaShim,
  connectWallet,
  readChainId,
  ensureChainId,
  onNewHeads,
  httpRpc,
} from "./services/provider";
import Contracts from "./pages/Contracts";
import "./styles.css";

export type HeadView = {
  number: number;
  hash: string;
  timestamp?: number;
};

const RPC_URL: string = (import.meta as any).env?.VITE_RPC_URL ?? "http://localhost:8545/rpc";
const DEFAULT_CHAIN_ID: number = Number((import.meta as any).env?.VITE_CHAIN_ID ?? "1337");

const App: React.FC = () => {
  const provider = useMemo(() => {
    installAnimicaShim();
    return getAnimicaProvider();
  }, []);

  const [account, setAccount] = useState<string | null>(null);
  const [chainId, setChainId] = useState<number>(DEFAULT_CHAIN_ID);
  const [head, setHead] = useState<HeadView | null>(null);
  const [balance, setBalance] = useState<string>("—");
  const [nonce, setNonce] = useState<number | null>(null);
  const [status, setStatus] = useState<string>("idle");
  const [rpcError, setRpcError] = useState<string | null>(null);

  // Detect provider events
  useEffect(() => {
    const unsub = onNewHeads((h) => setHead(h), provider);
    const onAccounts = (a: string[]) => setAccount(a?.[0] ?? null);
    const onChain = (cid: number | string) => setChainId(Number(cid));

    provider.on?.("accountsChanged", onAccounts);
    provider.on?.("chainChanged", onChain);

    return () => {
      unsub();
      provider.removeListener?.("accountsChanged", onAccounts);
      provider.removeListener?.("chainChanged", onChain);
    };
  }, [provider]);

  // Fallback head polling
  useEffect(() => {
    let timer: number | null = null;
    const tick = async () => {
      try {
        const h = await httpRpc<HeadView>("chain.getHead", undefined, RPC_URL);
        setHead(h);
        setRpcError(null);
      } catch (err: any) {
        setRpcError(err?.message ?? String(err));
      }
    };
    tick();
    timer = window.setInterval(tick, 2500);
    return () => {
      if (timer) window.clearInterval(timer);
    };
  }, []);

  // Connect wallet handler
  const onConnect = async () => {
    setStatus("connecting…");
    const acc = await connectWallet(provider);
    setAccount(acc);
    const cid = await readChainId(provider);
    setChainId(cid);
    setStatus(acc ? "connected" : "ready");
  };

  // Ensure chain
  const onEnsureChain = async () => {
    await ensureChainId(DEFAULT_CHAIN_ID, provider);
    setChainId(DEFAULT_CHAIN_ID);
  };

  // Fetch account state when account changes
  useEffect(() => {
    if (!account) return;
    (async () => {
      try {
        setStatus("loading account…");
        const [balRaw, nonceRaw] = await Promise.all([
          httpRpc<any>("state.getBalance", { address: account }, RPC_URL).catch(() => httpRpc<any>("state.getBalance", [account], RPC_URL)),
          httpRpc<number>("state.getNonce", { address: account }, RPC_URL).catch(() => httpRpc<number>("state.getNonce", [account], RPC_URL)),
        ]);
        setBalance(typeof balRaw === "object" && balRaw && "raw" in balRaw ? String((balRaw as any).raw) : String(balRaw ?? "—"));
        setNonce(nonceRaw ?? null);
        setStatus("ready");
      } catch (err: any) {
        setStatus("error");
        setRpcError(err?.message ?? String(err));
      }
    })();
  }, [account]);

  return (
    <div className="container" style={{ padding: "24px", maxWidth: 1180, margin: "0 auto" }}>
      <header style={{ marginBottom: 18 }}>
        <h1>Animica dApp Template</h1>
        <p>Detect the Animica provider, connect, monitor chain head, and interact with contracts.</p>
      </header>

      <section className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <div>
            <h2>Wallet</h2>
            <p>Uses the injected provider when available; falls back to the built-in shim.</p>
          </div>
          <div className="stack row">
            <button className="btn" onClick={onConnect} disabled={status === "connecting…"}>
              {status === "connecting…" ? "Connecting…" : "Connect"}
            </button>
            <button className="btn ghost" onClick={onEnsureChain} disabled={!account}>
              Ensure Chain ({DEFAULT_CHAIN_ID})
            </button>
          </div>
        </div>
        <div className="grid two" style={{ marginTop: 12 }}>
          <div><strong>Account:</strong> <code>{account ?? "—"}</code></div>
          <div><strong>ChainId:</strong> <code>{chainId}</code></div>
          <div><strong>Status:</strong> <span>{status}</span></div>
          <div><strong>RPC URL:</strong> <code>{RPC_URL}</code></div>
        </div>
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <div>
            <h2>Chain Head</h2>
            <p>Listens to <code>newHeads</code> and polls <code>chain.getHead</code> as a fallback.</p>
          </div>
        </div>
        {head ? (
          <div className="grid two">
            <div><strong>Height</strong><div><code>{head.number}</code></div></div>
            <div><strong>Hash</strong><div><code>{head.hash}</code></div></div>
            {head.timestamp != null && (
              <div style={{ gridColumn: "span 2" }}>
                <strong>Timestamp</strong>
                <div><code>{new Date(head.timestamp * 1000).toISOString()}</code></div>
              </div>
            )}
          </div>
        ) : (
          <p style={{ color: "var(--muted)" }}>Fetching latest head…</p>
        )}
        {rpcError && <p style={{ color: "var(--danger)", marginTop: 10 }}>RPC error: {rpcError}</p>}
      </section>

      <section className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <div>
            <h2>Account State</h2>
            <p>Reads balance and nonce via JSON-RPC.</p>
          </div>
        </div>
        {account ? (
          <div className="grid two">
            <div><strong>Balance</strong><div><code>{balance}</code></div></div>
            <div><strong>Nonce</strong><div><code>{nonce ?? "—"}</code></div></div>
          </div>
        ) : (
          <p style={{ color: "var(--muted)" }}>Connect a wallet to load account state.</p>
        )}
      </section>

      <section className="card">
        <div className="card-header">
          <div>
            <h2>Contract Interact</h2>
            <p>Calls <code>animica_callContract</code> / <code>animica_call</code> / <code>eth_call</code> with ABI payloads.</p>
          </div>
        </div>
        <Contracts />
      </section>
    </div>
  );
};

export default App;
