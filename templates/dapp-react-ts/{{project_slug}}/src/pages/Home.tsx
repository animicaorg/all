import React, { useCallback, useEffect, useMemo, useState } from "react";
import { createClient, transferViaWallet, type Head, type Address, type Receipt } from "../services/sdk";
import { getAnimicaProvider } from "../services/provider";

/**
 * Home.tsx — Starter page for the Animica dapp scaffold.
 *
 * Features:
 *  - Detect & connect to the Animica wallet provider (window.animica)
 *  - Display current head (block height/hash) with live updates
 *  - Show account address & balance
 *  - Minimal transfer form that asks wallet to sign+send and then waits for a receipt
 *
 * Notes:
 *  - The RPC URL comes from VITE_RPC_URL (see .env). You can override per-call if needed.
 *  - Addresses are bech32m ("anim1…") by default in Animica. If your node is configured
 *    differently (e.g. hex addresses), adapt formatting and validation in your app.
 */

function shorten(addr?: string, head = 6, tail = 6) {
  if (!addr) return "";
  if (addr.length <= head + tail + 3) return addr;
  return `${addr.slice(0, head)}…${addr.slice(-tail)}`;
}

function hexShort(h?: string, head = 10, tail = 6) {
  if (!h) return "";
  if (h.length <= head + tail + 3) return h;
  return `${h.slice(0, head)}…${h.slice(-tail)}`;
}

export default function Home() {
  // Client (HTTP JSON-RPC helper)
  const client = useMemo(() => createClient(), []);

  // Chain head state
  const [head, setHead] = useState<Head | null>(null);
  const [recentHeads, setRecentHeads] = useState<Head[]>([]);

  // Account state
  const [accounts, setAccounts] = useState<Address[]>([]);
  const [active, setActive] = useState<Address | null>(null);
  const [balance, setBalance] = useState<string | null>(null);
  const [nonce, setNonce] = useState<number | null>(null);

  // Transfer form
  const [to, setTo] = useState<Address>("anim1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"); // placeholder
  const [value, setValue] = useState<string>("1"); // smallest units as decimal string
  const [sending, setSending] = useState(false);
  const [txHash, setTxHash] = useState<string | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [error, setError] = useState<string | null>(null);

  /* ------------------------------ Head subscription ------------------------------ */

  useEffect(() => {
    let cancel = () => {};
    (async () => {
      try {
        // Initialize with current head then subscribe for updates
        const h = await client.getHead();
        setHead(h);
        setRecentHeads((prev) => [h, ...prev].slice(0, 6));

        cancel = client.subscribeHeads((nh) => {
          setHead(nh);
          setRecentHeads((prev) => {
            // Avoid dupes when polling/shim fires quickly
            if (prev.length > 0 && prev[0].hash === nh.hash) return prev;
            return [nh, ...prev].slice(0, 6);
          });
        });
      } catch (e: any) {
        // best-effort; surface in UI footer
        console.warn("subscribeHeads failed:", e?.message || e);
      }
    })();
    return () => cancel();
  }, [client]);

  /* ----------------------------- Accounts & balance ----------------------------- */

  const refreshAccount = useCallback(async (addr: Address) => {
    try {
      const [bal, n] = await Promise.all([
        client.getBalance(addr),
        client.getNonce(addr),
      ]);
      setBalance(bal.balance);
      setNonce(n);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, [client]);

  // Try to read existing accounts without prompting on mount
  useEffect(() => {
    (async () => {
      try {
        const provider = getAnimicaProvider();
        const accs = (await provider.request<string[]>({ method: "animica_accounts" })) ?? [];
        if (Array.isArray(accs) && accs.length > 0) {
          const a = accs[0] as Address;
          setAccounts(accs as Address[]);
          setActive(a);
          await refreshAccount(a);
        }
      } catch {
        // Not an error; just means user hasn't connected yet.
      }
    })();
  }, [refreshAccount]);

  // If the active account changes, refresh balance/nonce
  useEffect(() => {
    if (active) {
      refreshAccount(active);
    } else {
      setBalance(null);
      setNonce(null);
    }
  }, [active, refreshAccount]);

  const connectWallet = useCallback(async () => {
    setError(null);
    try {
      const provider = getAnimicaProvider();
      const accs = await provider.request<string[]>({ method: "animica_requestAccounts" });
      if (!Array.isArray(accs) || accs.length === 0) throw new Error("No accounts returned by wallet");
      const a = accs[0] as Address;
      setAccounts(accs as Address[]);
      setActive(a);
      await refreshAccount(a);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  }, [refreshAccount]);

  /* --------------------------------- Transfer ---------------------------------- */

  const onSend = useCallback(async (ev: React.FormEvent) => {
    ev.preventDefault();
    if (!active) {
      setError("Connect a wallet first.");
      return;
    }
    setError(null);
    setSending(true);
    setTxHash(null);
    setReceipt(null);
    try {
      const { hash, receipt } = await transferViaWallet({
        from: active,
        to,
        value,
        waitFor: { timeoutMs: 90_000, pollMs: 1_500 },
      });
      setTxHash(hash);
      setReceipt(receipt);
      // Refresh sender state after send
      await refreshAccount(active);
    } catch (e: any) {
      setError(e?.message || String(e));
    } finally {
      setSending(false);
    }
  }, [active, to, value, refreshAccount]);

  /* ---------------------------------- Render ----------------------------------- */

  return (
    <div style={{ maxWidth: 960, margin: "0 auto", padding: "24px" }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22 }}>Animica Dapp Starter</h1>
          <p style={{ margin: "6px 0", opacity: 0.75 }}>
            RPC: <code>{import.meta.env.VITE_RPC_URL ?? "(not set)"}</code>
          </p>
        </div>
        <div>
          {active ? (
            <div style={{ textAlign: "right" }}>
              <div style={{ fontWeight: 600 }}>Account</div>
              <div title={active}>{shorten(active, 10, 8)}</div>
              <button
                onClick={() => refreshAccount(active)}
                style={{ marginTop: 8, padding: "6px 10px", cursor: "pointer" }}
              >
                Refresh Balance
              </button>
            </div>
          ) : (
            <button onClick={connectWallet} style={{ padding: "8px 14px", cursor: "pointer", fontWeight: 600 }}>
              Connect Wallet
            </button>
          )}
        </div>
      </header>

      <section
        style={{
          display: "grid",
          gap: 16,
          gridTemplateColumns: "1fr",
          marginBottom: 24,
        }}
      >
        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 16 }}>
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>Head</h2>
            {head && <span style={{ fontFamily: "monospace" }}>#{head.number}</span>}
          </div>
          <div style={{ marginTop: 8 }}>
            {head ? (
              <dl style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: 6, columnGap: 12 }}>
                <dt style={{ opacity: 0.7 }}>Hash</dt>
                <dd style={{ margin: 0, fontFamily: "monospace" }} title={head.hash}>
                  {hexShort(head.hash)}
                </dd>
                <dt style={{ opacity: 0.7 }}>Timestamp</dt>
                <dd style={{ margin: 0 }}>
                  {head.timestamp ? new Date(head.timestamp * 1000).toLocaleString() : "—"}
                </dd>
              </dl>
            ) : (
              <div>Loading head…</div>
            )}
          </div>
          {recentHeads.length > 1 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontWeight: 600, marginBottom: 6 }}>Recent</div>
              <ul style={{ margin: 0, paddingLeft: 18 }}>
                {recentHeads.map((h) => (
                  <li key={h.hash} style={{ fontFamily: "monospace" }}>
                    #{h.number} — {hexShort(h.hash)}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        <div style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 16 }}>
          <h2 style={{ margin: 0, fontSize: 18 }}>Account</h2>
          {active ? (
            <dl style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: 6, columnGap: 12, marginTop: 8 }}>
              <dt style={{ opacity: 0.7 }}>Address</dt>
              <dd style={{ margin: 0, fontFamily: "monospace" }} title={active}>
                {shorten(active, 12, 10)}
              </dd>
              <dt style={{ opacity: 0.7 }}>Balance</dt>
              <dd style={{ margin: 0 }}>{balance ?? "…"}</dd>
              <dt style={{ opacity: 0.7 }}>Nonce</dt>
              <dd style={{ margin: 0 }}>{nonce ?? "…"}</dd>
            </dl>
          ) : (
            <p style={{ marginTop: 8, opacity: 0.8 }}>Connect a wallet to see account info.</p>
          )}
        </div>
      </section>

      <section style={{ border: "1px solid #e2e8f0", borderRadius: 8, padding: 16, marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Send Transfer (via wallet)</h2>
        <form onSubmit={onSend} style={{ marginTop: 12, display: "grid", gap: 10, maxWidth: 720 }}>
          <label style={{ display: "grid", gap: 6 }}>
            <span>Recipient Address</span>
            <input
              type="text"
              value={to}
              onChange={(e) => setTo(e.target.value as Address)}
              placeholder="anim1…"
              spellCheck={false}
              style={{ padding: "8px 10px", fontFamily: "monospace" }}
            />
          </label>
          <label style={{ display: "grid", gap: 6 }}>
            <span>Amount (smallest units)</span>
            <input
              type="text"
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="e.g. 1000"
              style={{ padding: "8px 10px" }}
            />
          </label>

          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="submit"
              disabled={!active || sending}
              style={{
                padding: "8px 14px",
                cursor: active && !sending ? "pointer" : "not-allowed",
                fontWeight: 600,
              }}
            >
              {sending ? "Sending…" : "Send"}
            </button>
            {!active && <span style={{ opacity: 0.75 }}>Connect a wallet first.</span>}
          </div>
        </form>

        {(txHash || receipt) && (
          <div style={{ marginTop: 14 }}>
            <div style={{ fontWeight: 600 }}>Last Transaction</div>
            <dl style={{ display: "grid", gridTemplateColumns: "140px 1fr", rowGap: 6, columnGap: 12 }}>
              <dt style={{ opacity: 0.7 }}>Hash</dt>
              <dd style={{ margin: 0, fontFamily: "monospace" }} title={txHash || undefined}>
                {txHash ? hexShort(txHash) : "—"}
              </dd>
              <dt style={{ opacity: 0.7 }}>Status</dt>
              <dd style={{ margin: 0 }}>{receipt?.status ?? (txHash ? "PENDING…" : "—")}</dd>
              {receipt?.blockNumber != null && (
                <>
                  <dt style={{ opacity: 0.7 }}>Block</dt>
                  <dd style={{ margin: 0 }}>#{receipt.blockNumber}</dd>
                </>
              )}
              {receipt?.gasUsed != null && (
                <>
                  <dt style={{ opacity: 0.7 }}>Gas Used</dt>
                  <dd style={{ margin: 0 }}>{receipt.gasUsed}</dd>
                </>
              )}
            </dl>
          </div>
        )}

        {error && (
          <div style={{ marginTop: 14, color: "#b91c1c" }}>
            <strong>Error:</strong> {error}
          </div>
        )}
      </section>

      <footer style={{ opacity: 0.7 }}>
        <small>
          Tip: the wallet/provider implements an AIP-1193-like interface (window.animica).
          You can expand this page to deploy contracts, call read methods, and subscribe to events.
        </small>
      </footer>
    </div>
  );
}
