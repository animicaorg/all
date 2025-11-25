import React, { useEffect, useMemo, useState } from "react";
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
        if (!stop && result) setHead(result);
      } catch (err: any) {
        if (!stop) {
          push({ kind: "error", message: `RPC ${String(err?.message || err)}` });
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

  return (
    <header className="topbar">
      <div className="left">
        <div className="brand">
          <span className="logo">◎</span>
          <span className="title">Animica Studio</span>
        </div>

        <div className="network">
          <label className="label">Network</label>
          <select
            className="select"
            onChange={onChangeNetwork}
            value={activePreset ? String(presets.indexOf(activePreset)) : ""}
          >
            {presets.map((p, i) => (
              <option key={p.name} value={i}>
                {p.name} (chain {p.chainId})
              </option>
            ))}
          </select>
        </div>

        <div className="rpc">
          <span className="label">RPC</span>
          <span className="mono" title={rpcUrl}>
            {rpcUrl}
          </span>
        </div>

        <div className="chain">
          <span className="label">ChainId</span>
          <span className="mono">{chainId ?? "—"}</span>
        </div>
      </div>

      <div className="right">
        <div className="head">
          <span className={`dot ${loadingHead ? "blink" : "ok"}`} />
          <span className="label">Head</span>
          <span className="mono">
            {head ? `#${head.height}` : loadingHead ? "…" : "—"}
          </span>
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

      <style jsx>{`
        .topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          height: 56px;
          padding: 0 16px;
          border-bottom: 1px solid var(--border-muted);
          background: var(--surface-elev-1);
          color: var(--fg-strong);
          position: sticky;
          top: 0;
          z-index: 100;
        }
        .left,
        .right {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        .brand {
          display: flex;
          align-items: center;
          gap: 8px;
          font-weight: 600;
        }
        .logo {
          font-size: 18px;
        }
        .title {
          letter-spacing: 0.2px;
        }
        .label {
          font-size: 12px;
          color: var(--fg-muted);
          margin-right: 6px;
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
          padding: 6px 8px;
          border-radius: 8px;
          border: 1px solid var(--border-muted);
          background: var(--surface);
          color: var(--fg);
        }
        .btn {
          border: 1px solid transparent;
          padding: 6px 12px;
          border-radius: 8px;
          cursor: pointer;
          font-weight: 600;
        }
        .btn.primary {
          background: var(--accent);
          color: var(--on-accent);
        }
        .btn.secondary {
          background: transparent;
          border-color: var(--border-muted);
          color: var(--fg);
        }
        .btn:disabled {
          opacity: 0.6;
          cursor: default;
        }
        .badge {
          font-family: ui-monospace, monospace;
          font-size: 12px;
          padding: 6px 8px;
          border: 1px solid var(--border-muted);
          border-radius: 999px;
          background: var(--surface);
          cursor: pointer;
          user-select: none;
        }
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 6px;
          background: var(--ok);
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
        @media (max-width: 920px) {
          .rpc {
            display: none;
          }
        }
        @media (max-width: 720px) {
          .chain {
            display: none;
          }
        }
      `}</style>
    </header>
  );
}
