import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNetwork } from "../state/network";
import { useAccount } from "../state/account";
import { useToasts } from "../state/toasts";

/**
 * TopBar — network/account controls + head height
 *
 * - Network selector populated from useNetwork().presets
 * - Connect/Disconnect via useAccount() which should wrap window.animica provider
 * - Displays current head height by polling chain.getHead over JSON-RPC
 */

type Head = { height: number; hash: string; time?: number };

function shorten(addr: string, chars = 4) {
  if (!addr) return "";
  return `${addr.slice(0, 6)}…${addr.slice(-chars)}`;
}

function isProbablyRpcUrl(url: string) {
  try {
    const u = new URL(url);
    return !!u.protocol;
  } catch {
    return false;
  }
}

export default function TopBar() {
  const { rpcUrl, chainId, presets, setNetwork } = useNetwork();
  const { address, connected, connecting, connect, disconnect } = useAccount();
  const { push } = useToasts();

  const [head, setHead] = useState<Head | null>(null);
  const [loadingHead, setLoadingHead] = useState(false);
  const lastPollError = useRef<string | null>(null);

  const activePreset = useMemo(() => {
    return presets.find((p) => p.rpcUrl === rpcUrl) ?? null;
  }, [rpcUrl, presets]);

  // Poll head every 5s (fallback when WS isn't available)
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;

    async function poll() {
      if (!rpcUrl || !isProbablyRpcUrl(rpcUrl)) return;
      try {
        setLoadingHead(true);
        const res = await fetch(rpcUrl, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "chain.getHead",
            params: []
          })
        });
        const json = await res.json();
        const result = json?.result as Head | undefined;
        if (!stop && result) {
          setHead(result);
          lastPollError.current = null;
        }
      } catch (err: any) {
        const msg = `RPC ${String(err?.message || err)}`;
        if (!stop) {
          setHead(null);
          if (lastPollError.current !== msg) {
            lastPollError.current = msg;
            push({ kind: "error", message: msg });
          }
        }
      } finally {
        if (!stop) setLoadingHead(false);
      }
      if (!stop) {
        // @ts-ignore - Node/DOM typing differences in some toolchains
        timer = setTimeout(poll, 5000);
      }
    }

    poll();
    return () => {
      stop = true;
      if (typeof timer !== "undefined") {
        // @ts-ignore
        clearTimeout(timer);
      }
    };
  }, [rpcUrl, push]);

  async function onConnect() {
    try {
      await connect();
      push({ kind: "success", message: "Wallet connected" });
    } catch (err: any) {
      push({ kind: "error", message: `Connect failed: ${String(err?.message || err)}` });
    }
  }

  function onDisconnect() {
    disconnect();
    push({ kind: "success", message: "Disconnected" });
  }

  function onChangeNetwork(e: React.ChangeEvent<HTMLSelectElement>) {
    const idx = Number(e.target.value);
    const preset = presets[idx];
    if (preset) {
      setNetwork(preset);
      push({ kind: "success", message: `Switched to ${preset.name} (chainId ${preset.chainId})` });
    }
  }

  async function copyAddress() {
    if (!address) return;
    await navigator.clipboard.writeText(address);
    push({ kind: "success", message: "Address copied" });
  }

  const headLabel = useMemo(() => {
    if (!rpcUrl) return "—";
    if (head) return `#${head.height}`;
    return loadingHead ? "…" : "RPC offline";
  }, [head, loadingHead, rpcUrl]);

  return (
    <header className="topbar">
      <div className="left">
        <div className="brand">
          <span className="logo">◎</span>
          <span className="title">Animica Studio</span>
        </div>

        <div className="group network">
          <span className="label">Network</span>
          <select
            className="select"
            onChange={onChangeNetwork}
            value={activePreset ? String(presets.indexOf(activePreset)) : ""}
          >
            {presets.map((p, i) => (
              <option key={`${p.name}-${p.chainId}-${i}`} value={i}>
                {p.name} (chain {p.chainId})
              </option>
            ))}
          </select>
        </div>

        <div className="group rpc">
          <span className="label">RPC</span>
          <span className="value mono" title={rpcUrl}>
            {rpcUrl}
          </span>
        </div>

        <div className="group chain">
          <span className="label">Chain</span>
          <span className="value mono">{chainId ?? "—"}</span>
        </div>
      </div>

      <div className="right">
        <div className="group head">
          <span className={`dot ${loadingHead ? "blink" : "ok"}`} />
          <span className="label">Head</span>
          <span className="value mono">{headLabel}</span>
        </div>

        {connected ? (
          <div className="account">
            <span className="badge" title={address || ""} onClick={copyAddress}>
              {address ? shorten(address) : "—"}
            </span>
            <button className="btn secondary" onClick={onDisconnect}>
              Disconnect
            </button>
          </div>
        ) : (
          <button className="btn primary" onClick={onConnect} disabled={connecting}>
            {connecting ? "Connecting…" : "Connect Wallet"}
          </button>
        )}
      </div>

      <style>{`
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: 64px;
          padding: 0 20px;
          border-bottom: 1px solid var(--border-muted);
          background: radial-gradient(circle at 20% 20%, color-mix(in oklab, var(--accent) 14%, transparent), transparent 45%),
            radial-gradient(circle at 80% 10%, color-mix(in oklab, var(--success) 12%, transparent), transparent 40%),
            var(--surface-elev-1);
          color: var(--fg-strong);
          position: sticky;
          top: 0;
          z-index: 100;
          box-shadow: 0 8px 20px rgba(0, 0, 0, 0.12);
        }
        .left,
        .right {
          display: flex;
          align-items: center;
          gap: 18px;
        }
        .brand {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          font-weight: 700;
          padding: 8px 12px;
          border-radius: 12px;
          background: color-mix(in oklab, var(--accent) 6%, transparent);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.08);
        }
        .logo {
          font-size: 18px;
        }
        .title {
          letter-spacing: 0.25px;
        }
        .group {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }
        .label {
          font-size: 12px;
          color: var(--fg-muted);
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }
        .value {
          display: inline-flex;
          align-items: center;
          gap: 8px;
          padding: 8px 10px;
          border-radius: 12px;
          background: var(--surface);
          border: 1px solid var(--border-muted);
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.04);
        }
        .mono {
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
            "Courier New", monospace;
          font-size: 12px;
        }
        .network,
        .rpc,
        .chain,
        .head {
          display: flex;
          align-items: center;
        }
        .select {
          padding: 8px 10px;
          border-radius: 10px;
          border: 1px solid var(--border-muted);
          background: var(--surface);
          color: var(--fg);
          min-width: 180px;
          box-shadow: 0 6px 16px rgba(0, 0, 0, 0.08);
        }
        .btn {
          border: 1px solid transparent;
          padding: 8px 14px;
          border-radius: 12px;
          cursor: pointer;
          font-weight: 700;
          transition: transform 120ms ease, box-shadow 120ms ease, background 120ms ease;
        }
        .btn.primary {
          background: linear-gradient(120deg, color-mix(in oklab, var(--accent) 96%, #fff 6%), var(--accent));
          color: var(--on-accent);
          box-shadow: 0 8px 18px color-mix(in oklab, var(--accent) 30%, transparent);
        }
        .btn.secondary {
          background: var(--surface);
          border-color: var(--border-muted);
          color: var(--fg);
        }
        .btn:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 10px 22px rgba(0, 0, 0, 0.12);
        }
        .btn:disabled {
          opacity: 0.6;
          cursor: default;
        }
        .badge {
          font-family: ui-monospace, monospace;
          font-size: 12px;
          padding: 7px 10px;
          border: 1px solid var(--border-muted);
          border-radius: 999px;
          background: color-mix(in oklab, var(--accent) 8%, var(--surface));
          cursor: pointer;
          user-select: none;
          box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.06);
        }
        .dot {
          width: 10px;
          height: 10px;
          border-radius: 50%;
          margin-right: 6px;
          background: var(--ok);
          box-shadow: 0 0 0 3px color-mix(in oklab, var(--accent) 18%, transparent);
        }
        .dot.ok {
          background: var(--ok);
        }
        .dot.blink {
          animation: blink 1s infinite;
          background: var(--accent);
        }
        @keyframes blink {
          0%,
          100% {
            opacity: 0.4;
          }
          50% {
            opacity: 1;
          }
        }
        @media (max-width: 1100px) {
          .rpc .value {
            display: none;
          }
        }
        @media (max-width: 820px) {
          .chain .value {
            display: none;
          }
        }
        @media (max-width: 720px) {
          .topbar {
            height: auto;
            flex-direction: column;
            align-items: flex-start;
            gap: 12px;
            padding: 14px;
          }
          .left,
          .right {
            width: 100%;
            justify-content: space-between;
          }
        }
      `}</style>
    </header>
  );
}
