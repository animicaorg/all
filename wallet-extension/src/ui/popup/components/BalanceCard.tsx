import React from "react";
import Copy from "../../shared/components/Copy";
import { useBalance } from "../../shared/hooks/useBalance";

type Props = {
  /** Optional: override address shown (otherwise uses selected account) */
  address?: string;
  /** Optional: compact layout (smaller font, tighter spacing) */
  compact?: boolean;
};

function shortAddr(addr: string) {
  if (!addr) return "";
  if (addr.length <= 12) return addr;
  return `${addr.slice(0, 6)}…${addr.slice(-6)}`;
}

function formatAmount(amount: bigint | number, decimals = 18, maxFrac = 6) {
  try {
    const n = typeof amount === "number" ? BigInt(Math.trunc(amount)) : amount;
    const neg = n < 0n;
    const abs = neg ? -n : n;
    const base = 10n ** BigInt(decimals);
    const int = abs / base;
    const frac = abs % base;

    // Build fractional string with left zero pad to length decimals, then trim trailing zeros
    let fracStr = frac.toString().padStart(decimals, "0").replace(/0+$/, "");
    if (fracStr.length > maxFrac) fracStr = fracStr.slice(0, maxFrac).replace(/0+$/, "");
    const body = fracStr.length ? `${int.toString()}.${fracStr}` : int.toString();
    return neg ? `-${body}` : body;
  } catch {
    return "0";
  }
}

export default function BalanceCard({ address: addressOverride, compact }: Props) {
  const { address, network, balance, symbol, decimals, loading, error, refresh, lastUpdatedAt } =
    useBalance(addressOverride);

  const classes = ["ami-card", "ami-balance"];
  if (compact) classes.push("ami-card-compact");

  return (
    <div className={classes.join(" ")}>
      <div className="ami-card-header">
        <div className="ami-card-title">Balance</div>
        <div className="ami-actions">
          <button
            className="ami-icon-btn"
            title="Refresh balance"
            onClick={refresh}
            aria-label="Refresh balance"
            disabled={loading}
          >
            ⟳
          </button>
        </div>
      </div>

      <div className="ami-balance-row">
        <div className="ami-balance-amount">
          {loading ? (
            <span className="ami-skeleton" style={{ width: 120, height: 24 }} />
          ) : error ? (
            <span className="ami-error">Error</span>
          ) : (
            <>
              <span className="ami-balance-value">
                {formatAmount(balance ?? 0n, decimals ?? 18)}
              </span>
              <span className="ami-balance-symbol">{symbol || "ANIM"}</span>
            </>
          )}
        </div>
        {lastUpdatedAt ? (
          <div className="ami-meta ami-dim">Updated {new Date(lastUpdatedAt).toLocaleTimeString()}</div>
        ) : null}
      </div>

      <div className="ami-divider" />

      <div className="ami-balance-meta">
        <div className="ami-meta-row">
          <div className="ami-meta-label">Account</div>
          <div className="ami-meta-value">
            <code className="ami-code">{shortAddr(address || "")}</code>
            {address ? <Copy text={address} className="ami-copy" /> : null}
          </div>
        </div>
        <div className="ami-meta-row">
          <div className="ami-meta-label">Network</div>
          <div className="ami-meta-value">
            {network?.name ?? "Unknown"}{" "}
            {typeof network?.chainId !== "undefined" ? (
              <span className="ami-dim">({network.chainId})</span>
            ) : null}
          </div>
        </div>
      </div>

      <style>{`
        .ami-balance .ami-balance-row { display:flex; align-items:baseline; justify-content:space-between; gap:.5rem; }
        .ami-balance .ami-balance-amount { display:flex; align-items:baseline; gap:.5rem; }
        .ami-balance .ami-balance-value { font-size: 1.4rem; font-weight: 600; }
        .ami-balance .ami-balance-symbol { font-size: .9rem; opacity: .8; }
        .ami-meta-row { display:flex; align-items:center; justify-content:space-between; gap:.5rem; padding:.25rem 0; }
        .ami-meta-label { opacity:.7; }
        .ami-code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
        .ami-divider { height:1px; background: var(--ami-border, rgba(0,0,0,.08)); margin:.5rem 0; }
        .ami-error { color: var(--ami-danger, #b00020); }
        .ami-skeleton { display:inline-block; background: linear-gradient(90deg, rgba(0,0,0,.08), rgba(0,0,0,.04), rgba(0,0,0,.08)); border-radius:4px; animation: ami-shimmer 1.2s infinite; }
        @keyframes ami-shimmer { 0%{background-position:-200px 0} 100%{background-position:200px 0} }
      `}</style>
    </div>
  );
}
