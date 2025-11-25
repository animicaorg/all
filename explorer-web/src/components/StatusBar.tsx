import React from "react";
import { useTranslation } from "react-i18next";
// These stores are provided by explorer-web/src/state/* in this repo.
import { useNetworkStore } from "../state/network";
import { usePeersStore } from "../state/peers";

type Props = {
  className?: string;
};

function fmtLatency(ms: number | null | undefined) {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1) return "<1 ms";
  return `${Math.round(ms)} ms`;
}

function badgeColorForLatency(ms: number | null | undefined) {
  if (ms == null || Number.isNaN(ms)) return "#9ca3af"; // gray
  if (ms < 120) return "#10b981"; // green
  if (ms < 300) return "#f59e0b"; // amber
  return "#ef4444"; // red
}

export default function StatusBar({ className }: Props) {
  const { t } = useTranslation();

  // Network: head height & latency come from the network store
  const headHeight = useNetworkStore((s) => s.head?.height ?? s.height ?? 0);
  const chainId = useNetworkStore((s) => s.chainId);
  const latencyMs = useNetworkStore((s) => s.latencyMs ?? s.pingMs ?? null);
  const rpcUrl = useNetworkStore((s) => s.rpcUrl);

  // Peers snapshot (count / health)
  const peerCount = usePeersStore((s) => s.count ?? s.peers?.length ?? 0);

  const color = badgeColorForLatency(latencyMs);

  return (
    <footer
      className={["status-bar", className].filter(Boolean).join(" ")}
      role="contentinfo"
      aria-label={t("statusBar.label", "Network Status")}
    >
      <div className="left">
        <span className="status-dot" aria-hidden="true" style={{ background: color, boxShadow: `0 0 0 2px ${color}22` }} />
        <span className="net">
          <span className="key">{t("statusBar.network", "Network")}:</span>
          <span className="val mono">{chainId ?? t("statusBar.unknown", "unknown")}</span>
        </span>

        <span className="sep" />

        <span className="height" title={t("statusBar.headHeightTitle", "Latest finalized block height") as string}>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" d="M3 3h8v8H3V3Zm10 0h8v8h-8V3ZM3 13h8v8H3v-8Zm10 0h8v8h-8v-8Z" />
          </svg>
          <span className="key">{t("statusBar.head", "Head")}:</span>
          <span className="val mono">#{headHeight || 0}</span>
        </span>

        <span className="sep" />

        <span className="peers" title={t("statusBar.peersTitle", "Connected peers") as string}>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" d="M12 3a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm-7 9a4 4 0 1 1 0 8 4 4 0 0 1 0-8Zm14 0a4 4 0 1 1 0 8 4 4 0 0 1 0-8Z" />
          </svg>
          <span className="key">{t("statusBar.peers", "Peers")}:</span>
          <span className="val mono">{peerCount}</span>
        </span>
      </div>

      <div className="right">
        <span className="latency" title={t("statusBar.latencyTitle", "RPC round-trip latency (p50)") as string}>
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" d="M12 1a11 11 0 1 0 .001 22.001A11 11 0 0 0 12 1Zm1 11V6h-2v8h7v-2h-5Z" />
          </svg>
          <span className="key">{t("statusBar.latency", "Latency")}:</span>
          <span className="val mono" style={{ color }}>{fmtLatency(latencyMs)}</span>
        </span>

        <span className="sep" />

        <span className="rpc mono" title={rpcUrl}>
          {rpcUrl}
        </span>
      </div>

      <style>{css}</style>
    </footer>
  );
}

// Inline CSS keeps the component portable and avoids global coupling.
const css = `
.status-bar {
  position: sticky;
  bottom: 0;
  z-index: 20;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 12px;
  border-top: 1px solid var(--border-muted, #e5e7eb);
  background: var(--bg-elev-0, #ffffff);
  color: var(--text, #111827);
  font-size: 12px;
}

.status-bar .left,
.status-bar .right{
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.status-bar .status-dot{
  width: 8px;
  height: 8px;
  border-radius: 999px;
}

.status-bar .sep{
  width: 1px;
  height: 14px;
  background: var(--border-muted, #e5e7eb);
}

.status-bar .key{
  opacity: .7;
  margin: 0 4px 0 6px;
}

.status-bar .val{
  font-weight: 600;
}

.status-bar svg{
  margin-right: 4px;
  color: var(--text-muted, #6b7280);
}

.status-bar .mono{
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  letter-spacing: .2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

@media (max-width: 920px){
  .status-bar .rpc{ display: none; }
  .status-bar .key{ display: none; }
}
`;
