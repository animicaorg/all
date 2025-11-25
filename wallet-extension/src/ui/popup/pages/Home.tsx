import React, { useEffect, useMemo, useState } from "react";
// Will be created later in the sequence:
import TxList from "../components/TxList";

type TxStatus = "pending" | "confirmed" | "failed";

export type TxListItem = {
  hash: string;
  direction: "in" | "out";
  amount: string; // human-readable (e.g., "1.23 ANM")
  to?: string;
  from?: string;
  status: TxStatus;
  timestamp?: number; // epoch ms
};

type HeadInfo = {
  height: number;
  chainId: number;
  networkName?: string;
};

function callBackground<T = any>(msg: any): Promise<T> {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (resp) => resolve(resp as T));
    } catch {
      // Dev fallback
      resolve(undefined as unknown as T);
    }
  });
}

export default function Home() {
  const [head, setHead] = useState<HeadInfo | null>(null);
  const [txs, setTxs] = useState<TxListItem[] | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = async () => {
    setLoading(true);
    try {
      const h = await callBackground<HeadInfo>({ type: "rpc.getHead" });
      if (h && typeof h.height === "number") setHead(h);

      const recent = await callBackground<TxListItem[]>({
        type: "wallet.getRecentTxs",
        limit: 10,
      });
      if (Array.isArray(recent)) setTxs(recent);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const title = useMemo(() => {
    if (!head) return "Overview";
    const n = head.networkName ? ` • ${head.networkName}` : "";
    return `Overview • #${head.height}${n}`;
  }, [head]);

  const switchTab = (to: "send" | "receive") => {
    try {
      chrome.runtime?.sendMessage?.({ type: "popup.switchTab", to });
    } catch {
      /* noop in dev */
    }
  };

  return (
    <section className="ami-home">
      <div className="ami-section-header">
        <h2 className="ami-section-title">{title}</h2>
        <button
          onClick={refresh}
          className="ami-btn"
          aria-busy={loading}
          disabled={loading}
          title="Refresh"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {/* Quick actions */}
      <div className="ami-quick-actions">
        <button className="ami-qa" onClick={() => switchTab("send")}>
          <span className="ami-qa-emoji" aria-hidden>
            📤
          </span>
          <span>Send</span>
        </button>
        <button className="ami-qa" onClick={() => switchTab("receive")}>
          <span className="ami-qa-emoji" aria-hidden>
            📥
          </span>
          <span>Receive</span>
        </button>
      </div>

      {/* Latest activity */}
      <div className="ami-section-header">
        <h3 className="ami-section-subtitle">Latest activity</h3>
      </div>

      {txs && txs.length > 0 ? (
        <TxList items={txs} />
      ) : txs && txs.length === 0 ? (
        <div className="ami-empty">
          <div className="ami-empty-emoji" aria-hidden>
            💤
          </div>
          <div className="ami-empty-title">No recent activity</div>
          <div className="ami-empty-subtitle">Your latest transactions will appear here.</div>
        </div>
      ) : (
        <div className="ami-skeleton-list">
          <div className="ami-skeleton-row" />
          <div className="ami-skeleton-row" />
          <div className="ami-skeleton-row" />
        </div>
      )}
    </section>
  );
}
