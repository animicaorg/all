import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getAnimicaProvider } from "../services/provider";

/**
 * TxStatus.tsx — Observe a transaction by hash and render live status.
 *
 * Polls the node for:
 *   - tx.getTransactionReceipt (primary)
 *   - tx.getTransactionByHash   (fallback metadata)
 *
 * States:
 *   - "idle":     no hash provided
 *   - "pending":  no receipt yet
 *   - "success":  receipt.status truthy or "SUCCESS"
 *   - "reverted": receipt.status falsy or "REVERT"/"OOG"
 *   - "unknown":  couldn't determine (API error, not found for a long time)
 *
 * Usage:
 *   <TxStatus txHash={hash} pollIntervalMs={2500} explorerBaseUrl="https://explorer.dev.animica.org" />
 */

type StatusKind = "idle" | "pending" | "success" | "reverted" | "unknown";

export type TxStatusProps = {
  /** 0x-prefixed tx hash */
  txHash?: string;
  /** polling interval in ms (default: 3000) */
  pollIntervalMs?: number;
  /** show decoded receipt/logs summary if available */
  showDetails?: boolean;
  /** compact pill vs card layout */
  compact?: boolean;
  /** optional explorer base URL to render a link (e.g., https://explorer.devnet.animica.org) */
  explorerBaseUrl?: string;
  /** callback when the tx moves to a terminal state (success|reverted) */
  onSettled?: (state: { kind: StatusKind; receipt: any | null }) => void;
};

type ReceiptLike = {
  status?: boolean | string | number;
  blockNumber?: number | string | null;
  gasUsed?: string | number | null;
  logs?: Array<any> | null;
  [k: string]: any;
};

type TxMeta = {
  blockNumber?: number | string | null;
  [k: string]: any;
};

function normalizeStatusFlag(status: unknown): "success" | "reverted" | "unknown" {
  if (typeof status === "boolean") return status ? "success" : "reverted";
  if (typeof status === "number") return status !== 0 ? "success" : "reverted";
  if (typeof status === "string") {
    const s = status.toUpperCase();
    if (s === "SUCCESS" || s === "OK" || s === "1") return "success";
    if (s === "REVERT" || s === "OOG" || s === "FAIL" || s === "0") return "reverted";
  }
  return "unknown";
}

function hexPad(n: number, width = 2): string {
  const s = n.toString(16);
  return "0x" + s.padStart(width, "0");
}

function shorten(hex?: string, head = 10, tail = 8) {
  if (!hex) return "";
  if (hex.length <= head + tail + 3) return hex;
  return `${hex.slice(0, head)}…${hex.slice(-tail)}`;
}

export default function TxStatus({
  txHash,
  pollIntervalMs = 3000,
  showDetails = true,
  compact = false,
  explorerBaseUrl,
  onSettled,
}: TxStatusProps) {
  const provider = useMemo(() => getAnimicaProvider(), []);
  const [kind, setKind] = useState<StatusKind>("idle");
  const [receipt, setReceipt] = useState<ReceiptLike | null>(null);
  const [txMeta, setTxMeta] = useState<TxMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<number | null>(null);
  const timerRef = useRef<number | null>(null);
  const settledRef = useRef<boolean>(false);

  const card: React.CSSProperties = {
    border: "1px solid #e2e8f0",
    borderRadius: 8,
    padding: 12,
    background: "white",
  };
  const pill: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    border: "1px solid #e2e8f0",
    borderRadius: 999,
    padding: "6px 10px",
    background: "white",
  };
  const row: React.CSSProperties = { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" };
  const badge: React.CSSProperties = { padding: "2px 8px", borderRadius: 999, fontSize: 12, fontWeight: 700 };

  const badgeStyle = (k: StatusKind): React.CSSProperties => {
    const base = { ...badge, color: "#111827", background: "#e5e7eb", border: "1px solid #d1d5db" };
    if (k === "pending") return { ...badge, color: "#92400e", background: "#fef3c7", border: "1px solid #f59e0b" };
    if (k === "success") return { ...badge, color: "#065f46", background: "#d1fae5", border: "1px solid #10b981" };
    if (k === "reverted") return { ...badge, color: "#991b1b", background: "#fee2e2", border: "1px solid #ef4444" };
    if (k === "unknown") return { ...badge, color: "#1f2937", background: "#e5e7eb", border: "1px solid #9ca3af" };
    return base;
  };

  const explorerHref = useMemo(() => {
    if (!explorerBaseUrl || !txHash) return null;
    const trimmed = explorerBaseUrl.replace(/\/+$/, "");
    return `${trimmed}/tx/${txHash}`;
  }, [explorerBaseUrl, txHash]);

  const clearTimer = () => {
    if (timerRef.current != null) window.clearInterval(timerRef.current);
    timerRef.current = null;
  };

  const classify = useCallback((r: ReceiptLike | null): StatusKind => {
    if (!txHash) return "idle";
    if (r == null) return "pending";
    const nk = normalizeStatusFlag(r.status);
    if (nk === "success") return "success";
    if (nk === "reverted") return "reverted";
    // If status unknown but has a blockNumber, assume inclusion with unknown outcome
    if (r.blockNumber != null) return "success";
    return "unknown";
  }, [txHash]);

  const tick = useCallback(async () => {
    if (!txHash) {
      setKind("idle");
      setReceipt(null);
      setTxMeta(null);
      setError(null);
      setLastUpdated(Date.now());
      return;
    }
    try {
      setError(null);
      // Primary: receipt
      const rcp = (await provider
        .request<ReceiptLike | null>({ method: "tx.getTransactionReceipt", params: { hash: txHash } })
        .catch(() => null)) as ReceiptLike | null;

      // Secondary: tx meta (for blockNumber while pending)
      const tx = (await provider
        .request<TxMeta | null>({ method: "tx.getTransactionByHash", params: { hash: txHash } })
        .catch(() => null)) as TxMeta | null;

      setReceipt(rcp);
      setTxMeta(tx || null);

      const k = classify(rcp);
      setKind(k);
      setLastUpdated(Date.now());

      if (!settledRef.current && (k === "success" || k === "reverted")) {
        settledRef.current = true;
        onSettled?.({ kind: k, receipt: rcp });
      }
    } catch (e: any) {
      setError(e?.message ?? "Failed to query tx.");
      setKind((prev) => (prev === "success" || prev === "reverted" ? prev : "unknown"));
      setLastUpdated(Date.now());
    }
  }, [provider, txHash, classify, onSettled]);

  useEffect(() => {
    // reset when hash changes
    settledRef.current = false;
    setKind(txHash ? "pending" : "idle");
    setReceipt(null);
    setTxMeta(null);
    setError(null);
    setLastUpdated(Date.now());
    clearTimer();

    // start polling
    void tick();
    timerRef.current = window.setInterval(() => void tick(), Math.max(1000, pollIntervalMs));

    return () => {
      clearTimer();
    };
  }, [txHash, pollIntervalMs, tick]);

  const copyHash = useCallback(async () => {
    if (!txHash) return;
    try {
      await navigator.clipboard.writeText(txHash);
    } catch {
      /* ignore */
    }
  }, [txHash]);

  const blockNum =
    (receipt?.blockNumber ?? txMeta?.blockNumber) != null
      ? String(receipt?.blockNumber ?? txMeta?.blockNumber)
      : null;

  const header = (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={badgeStyle(kind)}>{kind.toUpperCase()}</span>
        {txHash && (
          <code style={{ fontFamily: "monospace" }} title={txHash}>
            {shorten(txHash)}
          </code>
        )}
        {txHash && (
          <button onClick={copyHash} style={{ padding: "4px 8px", cursor: "pointer" }} title="Copy tx hash">
            Copy
          </button>
        )}
        {explorerHref && (
          <a href={explorerHref} target="_blank" rel="noreferrer" style={{ textDecoration: "underline" }}>
            Open in Explorer
          </a>
        )}
      </div>
      <div style={{ fontSize: 12, opacity: 0.7 }}>
        {lastUpdated ? `Updated ${Math.round((Date.now() - lastUpdated) / 1000)}s ago` : ""}
      </div>
    </div>
  );

  const info = (
    <div style={row}>
      <div>
        <div style={{ fontSize: 12, opacity: 0.7 }}>Block</div>
        <code style={{ fontFamily: "monospace" }}>{blockNum ?? "—"}</code>
      </div>
      <div style={{ width: 1, height: 28, background: "#e5e7eb" }} />
      <div>
        <div style={{ fontSize: 12, opacity: 0.7 }}>Gas Used</div>
        <code style={{ fontFamily: "monospace" }}>
          {receipt?.gasUsed != null ? String(receipt.gasUsed) : "—"}
        </code>
      </div>
      <div style={{ width: 1, height: 28, background: "#e5e7eb" }} />
      <div>
        <div style={{ fontSize: 12, opacity: 0.7 }}>Status Raw</div>
        <code style={{ fontFamily: "monospace" }}>
          {receipt?.status != null ? String(receipt.status) : kind === "pending" ? "pending" : "—"}
        </code>
      </div>
      {error && (
        <>
          <div style={{ width: 1, height: 28, background: "#e5e7eb" }} />
          <div style={{ color: "#b45309" }}>⚠ {error}</div>
        </>
      )}
      <div style={{ flex: 1 }} />
      <button onClick={() => void tick()} style={{ padding: "6px 10px", cursor: "pointer" }}>
        Refresh
      </button>
    </div>
  );

  const details =
    showDetails && receipt?.logs && Array.isArray(receipt.logs) ? (
      <div style={{ marginTop: 8 }}>
        <div style={{ fontWeight: 600, marginBottom: 4 }}>Logs</div>
        <div style={{ display: "grid", gap: 6 }}>
          {receipt.logs.length === 0 ? (
            <div style={{ opacity: 0.7 }}>No logs.</div>
          ) : (
            receipt.logs.map((lg: any, idx: number) => (
              <div
                key={idx}
                style={{ border: "1px dashed #e5e7eb", borderRadius: 6, padding: 8, fontFamily: "monospace" }}
              >
                <div>#{idx}</div>
                <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
{JSON.stringify(
  {
    address: lg?.address,
    topics: lg?.topics,
    data: lg?.data,
    index: lg?.logIndex ?? lg?.index,
  },
  null,
  2
)}
                </pre>
              </div>
            ))
          )}
        </div>
      </div>
    ) : null;

  const content = (
    <div style={{ display: "grid", gap: 8 }}>
      {header}
      {info}
      {details}
    </div>
  );

  if (compact) return <div style={pill}>{content}</div>;
  return (
    <section style={card}>
      <div style={{ display: "grid", gap: 8 }}>
        <div style={{ fontWeight: 600 }}>Transaction</div>
        {content}
      </div>
    </section>
  );
}
