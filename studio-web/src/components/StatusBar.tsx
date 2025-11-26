import React, { useMemo } from "react";
import { useStore } from "../state/store";

/**
 * StatusBar — shows compile status, diagnostics, gas estimate,
 * current head height/hash, and connection health.
 *
 * Expects Zustand slices shaped roughly as:
 *  - state.compile = { status: 'idle'|'compiling'|'success'|'error', diagnostics: {severity:'error'|'warn'|'info', message:string}[], gasEstimate?: number }
 *  - state.network = { connected:boolean, latencyMs?: number, head?: { height:number, hash:string }, chainId?: number }
 */

function shortenHash(h?: string, width = 10) {
  if (!h) return "";
  if (h.length <= width + 2) return h;
  return `${h.slice(0, Math.ceil(width / 2))}…${h.slice(-Math.floor(width / 2))}`;
}

export default function StatusBar() {
  const compile = useStore((s) => s.compile);
  const network = useStore((s) => s.network);

  const diagCounts = useMemo(() => {
    const errs = compile?.diagnostics?.filter((d: any) => d.severity === "error").length ?? 0;
    const warns = compile?.diagnostics?.filter((d: any) => d.severity === "warn").length ?? 0;
    return { errs, warns };
  }, [compile?.diagnostics]);

  const statusLabel = useMemo(() => {
    switch (compile?.status) {
      case "compiling":
        return "Compiling…";
      case "success":
        return "Compiled";
      case "error":
        return "Errors";
      case "idle":
      default:
        return "Idle";
    }
  }, [compile?.status]);

  const statusClass = useMemo(() => {
    switch (compile?.status) {
      case "compiling":
        return "st-compiling";
      case "success":
        return "st-ok";
      case "error":
        return "st-error";
      default:
        return "st-idle";
    }
  }, [compile?.status]);

  const connected = !!network?.connected;
  const headHeight = network?.head?.height ?? 0;
  const headHash = network?.head?.hash ?? "";
  const latency = network?.latencyMs;

  return (
    <footer className="statusbar" role="status" aria-live="polite">
      <div className={`block compile ${statusClass}`}>
        <span className="icon" aria-hidden="true">
          {compile?.status === "compiling" ? "⏳" : compile?.status === "success" ? "✅" : compile?.status === "error" ? "🛑" : "💤"}
        </span>
        <span className="label">{statusLabel}</span>

        <div className="sep" />

        <span className="kv">
          <span className="k">gas est</span>
          <span className="v">
            {typeof compile?.gasEstimate === "number" ? compile.gasEstimate.toLocaleString() : "—"}
          </span>
        </span>

        <span className="kv">
          <span className="k">diag</span>
          <span className="v">
            {diagCounts.errs > 0 ? `${diagCounts.errs} err` : "0 err"}
            {diagCounts.warns > 0 ? `, ${diagCounts.warns} warn` : ""}
          </span>
        </span>
      </div>

      <div className="spacer" />

      <div className={`block network ${connected ? "ok" : "down"}`} title={connected ? "Connected" : "Disconnected"}>
        <span className="dot" aria-hidden="true" />
        <span className="label">{connected ? "Connected" : "Offline"}</span>

        <div className="sep" />

        <span className="kv">
          <span className="k">height</span>
          <span className="v">{headHeight ? headHeight.toLocaleString() : "—"}</span>
        </span>

        <span className="kv hide-sm">
          <span className="k">head</span>
          <span className="v mono">{shortenHash(headHash, 12) || "—"}</span>
        </span>

        <span className="kv">
          <span className="k">latency</span>
          <span className="v">{typeof latency === "number" ? `${Math.round(latency)} ms` : "—"}</span>
        </span>

        {typeof network?.chainId === "number" && (
          <span className="kv hide-sm">
            <span className="k">chain</span>
            <span className="v">#{network.chainId}</span>
          </span>
        )}
      </div>

      <style>{`
        .statusbar {
          position: fixed;
          left: 0;
          right: 0;
          bottom: 0;
          height: 36px;
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 0 12px;
          background: var(--surface);
          border-top: 1px solid var(--border-muted);
          color: var(--fg-muted);
          font-size: 12.5px;
          z-index: 20;
        }
        .block {
          display: inline-flex;
          align-items: center;
          gap: 10px;
          padding: 6px 10px;
          border: 1px solid var(--border-muted);
          border-radius: 10px;
          background: var(--surface-elev-1);
          color: var(--fg);
        }
        .compile.st-compiling {
          border-color: var(--accent);
        }
        .compile.st-ok {
          border-color: var(--success);
        }
        .compile.st-error {
          border-color: var(--danger);
        }
        .icon {
          font-size: 14px;
        }
        .sep {
          width: 1px;
          height: 16px;
          background: var(--border-muted);
        }
        .kv {
          display: inline-flex;
          align-items: center;
          gap: 6px;
        }
        .kv .k {
          color: var(--fg-muted);
          text-transform: uppercase;
          letter-spacing: 0.04em;
          font-size: 11px;
        }
        .kv .v {
          color: var(--fg);
          font-variant-numeric: tabular-nums;
        }
        .mono {
          font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono",
            "Courier New", monospace;
        }
        .network.ok .dot {
          background: var(--success);
          box-shadow: 0 0 0 2px color-mix(in oklab, var(--success) 30%, transparent);
        }
        .network.down .dot {
          background: var(--danger);
          box-shadow: 0 0 0 2px color-mix(in oklab, var(--danger) 30%, transparent);
        }
        .network .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
        }
        .label {
          font-weight: 600;
        }
        .spacer {
          flex: 1 1 auto;
        }
        @media (max-width: 880px) {
          .hide-sm {
            display: none;
          }
          .statusbar {
            padding: 0 8px;
          }
        }
      `}</style>
    </footer>
  );
}
