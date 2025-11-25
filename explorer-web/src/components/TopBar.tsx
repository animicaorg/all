import React, { useCallback, useMemo, useState } from "react";
import { useNavigate, Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

// State: network/head/latency come from the explorer store
// (See src/state/network.ts). We keep the types loose here to
// avoid tight coupling with store internals.
import { useNetworkStore } from "../state/network";

// Utils for formatting (optional, safe if not present)
import { shortHash } from "../utils/format";

// Small status dot
function StatusDot({ status }: { status: "connected" | "connecting" | "disconnected" | undefined }) {
  const color =
    status === "connected" ? "#16a34a" : status === "connecting" ? "#f59e0b" : "#ef4444";
  const label =
    status === "connected" ? "WS: online" : status === "connecting" ? "WS: connecting" : "WS: offline";
  return (
    <span title={label} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <span
        aria-label={label}
        style={{
          width: 10,
          height: 10,
          borderRadius: 999,
          background: color,
          boxShadow: `0 0 0 2px ${color}22`,
          display: "inline-block",
        }}
      />
    </span>
  );
}

// Parse search query into a route target
function parseSearch(qRaw: string): { kind: "tx" | "address" | "block" | "unknown"; value: string } {
  const q = qRaw.trim();

  // Hex helpers
  const isHex = (s: string) => /^(0x)?[0-9a-fA-F]+$/.test(s);
  const strip0x = (s: string) => (s.startsWith("0x") || s.startsWith("0X") ? s.slice(2) : s);

  // TX hash: 32 bytes
  if (isHex(q) && strip0x(q).length === 64) return { kind: "tx", value: q.toLowerCase() };

  // Address: bech32 (omni1...) — allow 39+ chars
  if (/^omni1[02-9ac-hj-np-z]{38,}$/i.test(q)) return { kind: "address", value: q };

  // Address: 20-byte hex (EVM-like)
  if (isHex(q) && strip0x(q).length === 40) return { kind: "address", value: q.toLowerCase() };

  // Block height: integer
  if (/^\d+$/.test(q)) return { kind: "block", value: q };

  return { kind: "unknown", value: q };
}

export default function TopBar() {
  const { t } = useTranslation();
  const navigate = useNavigate();

  // Network store bindings (kept tolerant to store changes)
  const presets = useNetworkStore((s: any) => s.presets ?? []);
  const current = useNetworkStore((s: any) => s.current ?? null);
  const switchNetwork = useNetworkStore((s: any) => s.switchNetwork ?? (() => {}));
  const rpcLatency = useNetworkStore((s: any) => s.latencyRpc as number | undefined);
  const wsStatus = useNetworkStore((s: any) => s.wsStatus as "connected" | "connecting" | "disconnected" | undefined);
  const head = useNetworkStore((s: any) => s.head as number | undefined);
  const finalized = useNetworkStore((s: any) => s.finalized as number | undefined);

  const [query, setQuery] = useState("");

  const onSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const parsed = parseSearch(query);
      if (parsed.kind === "tx") {
        navigate(`/tx/${parsed.value}`);
      } else if (parsed.kind === "address") {
        navigate(`/address/${parsed.value}`);
      } else if (parsed.kind === "block") {
        navigate(`/block/${parsed.value}`);
      } else {
        // Default to tx search page
        navigate(`/tx/${query}`);
      }
    },
    [navigate, query]
  );

  const headText = useMemo(() => {
    if (head == null) return t("labels.head", "Head");
    if (finalized == null) return `#${head}`;
    return `#${head} · ${t("labels.finalized", "Finalized")} #${finalized}`;
  }, [head, finalized, t]);

  const rpcText = useMemo(() => {
    if (rpcLatency == null) return "—";
    return `${rpcLatency.toFixed(0)} ${t("units.ms", "ms")}`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rpcLatency]);

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 40,
        display: "grid",
        gridTemplateColumns: "1fr 2fr 1fr",
        gap: 12,
        alignItems: "center",
        padding: "10px 14px",
        borderBottom: "1px solid var(--border-muted, #e5e7eb)",
        background: "var(--bg-elev-1, #fff)",
      }}
    >
      {/* Left: Logo + Network switcher */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, minWidth: 0 }}>
        <Link
          to="/"
          style={{
            fontWeight: 700,
            fontSize: 16,
            textDecoration: "none",
            color: "var(--text-strong, #111827)",
            whiteSpace: "nowrap",
          }}
          title={t("app.title", "Animica Explorer")}
        >
          Animica
        </Link>

        <div style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
          <label htmlFor="network" style={{ fontSize: 12, opacity: 0.8 }}>
            {t("app.network", "Network")}
          </label>
          <select
            id="network"
            value={current?.id ?? ""}
            onChange={(e) => switchNetwork(e.target.value)}
            style={{
              fontSize: 13,
              padding: "6px 8px",
              borderRadius: 8,
              border: "1px solid var(--border, #d1d5db)",
              background: "var(--bg, #fff)",
              color: "var(--text, #111827)",
              maxWidth: 220,
            }}
          >
            {presets.map((p: any) => (
              <option key={p.id} value={p.id} title={p.rpcUrl}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Center: Search */}
      <form onSubmit={onSubmit} style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            border: "1px solid var(--border, #d1d5db)",
            borderRadius: 10,
            padding: "6px 10px",
            gap: 8,
            background: "var(--bg, #fff)",
          }}
        >
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            aria-hidden="true"
            focusable="false"
            style={{ opacity: 0.6 }}
          >
            <path
              fill="currentColor"
              d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5Zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14Z"
            />
          </svg>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={
              t("tx.searchPlaceholder", "Search by hash, address or height…") as string
            }
            aria-label={t("common.search", "Search")}
            style={{
              flex: 1,
              border: "none",
              outline: "none",
              background: "transparent",
              fontSize: 14,
              minWidth: 0,
            }}
          />
          <button
            type="submit"
            style={{
              fontSize: 13,
              fontWeight: 600,
              padding: "6px 10px",
              borderRadius: 8,
              border: "1px solid var(--border, #d1d5db)",
              background: "var(--bg-elev-2, #f9fafb)",
              cursor: "pointer",
            }}
          >
            {t("common.search", "Search")}
          </button>
        </div>
      </form>

      {/* Right: Head badge + RPC/WS status */}
      <div
        style={{
          display: "flex",
          justifyContent: "flex-end",
          alignItems: "center",
          gap: 12,
          minWidth: 0,
          whiteSpace: "nowrap",
        }}
      >
        <div
          title={t("network.health.rpcLatency", "RPC Latency")}
          style={{
            fontSize: 12,
            padding: "4px 8px",
            borderRadius: 999,
            border: "1px solid var(--border, #d1d5db)",
            background: "var(--bg-elev-2, #f9fafb)",
          }}
        >
          RPC {rpcText}
        </div>
        <StatusDot status={wsStatus} />
        <Link
          to="/blocks"
          style={{
            fontSize: 12,
            padding: "4px 10px",
            borderRadius: 999,
            border: "1px solid var(--border-strong, #cbd5e1)",
            background: "var(--bg-elev-1, #fff)",
            color: "inherit",
            textDecoration: "none",
          }}
          title={t("blocks.title", "Blocks")}
        >
          {t("labels.head", "Head")}: {headText}
        </Link>
      </div>
    </header>
  );
}
