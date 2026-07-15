import React, { useState } from "react";

interface PendingSignature {
  requestId: string;
  origin: string;
  address: string;
  message: string;
  createdAt: number;
}

interface Props {
  pending: PendingSignature;
  onResolved: () => void;
}

// Explicit approval for a dapp's signMessage request. Signing is a post-quantum signing oracle over
// the wallet key, so the user must see WHO is asking and EXACTLY what they'd sign, and choose. If
// this popup is dismissed the background rejects the request (fail-closed) — nothing is signed.
export default function SignApprove({ pending, onResolved }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function respond(approved: boolean) {
    setBusy(true);
    setError(null);
    try {
      const res = await chrome.runtime.sendMessage({
        method: "wallet_signRespond",
        params: { requestId: pending.requestId, approved },
      });
      if (res?.error) throw new Error(res.error);
      onResolved();
    } catch (e: any) {
      setError(e?.message || "Could not respond.");
      setBusy(false);
    }
  }

  const host = (() => {
    try { return new URL(pending.origin).host; } catch { return pending.origin; }
  })();

  // Flag messages that look like they belong to another Animica protocol, so a user notices an
  // attempt to mint a login/session/usage signature they didn't intend.
  const looksSensitive = /^(animica-login\||usage:|abuse:)/.test(pending.message.trim());

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h2 style={styles.title}>Signature request</h2>
        <p style={styles.lede}>A site is asking your wallet to sign a message.</p>

        <div style={styles.originBox}>
          <div style={styles.originLabel}>SITE</div>
          <div style={styles.originValue}>{host}</div>
        </div>

        <div style={styles.originBox}>
          <div style={styles.originLabel}>SIGNING ACCOUNT</div>
          <div style={styles.originValue}>{pending.address.slice(0, 14)}…{pending.address.slice(-6)}</div>
        </div>

        <div style={styles.msgLabel}>MESSAGE</div>
        <pre style={styles.message}>{pending.message || "(empty)"}</pre>

        {looksSensitive ? (
          <p style={styles.warn}>
            ⚠ This looks like an Animica login or session message. Only approve if you started this action
            yourself — approving lets the site act as you.
          </p>
        ) : null}

        {error ? <p style={styles.error}>{error}</p> : null}

        <div style={styles.actions}>
          <button type="button" onClick={() => respond(false)} disabled={busy} style={styles.denyBtn}>
            Reject
          </button>
          <button type="button" onClick={() => respond(true)} disabled={busy} style={{ ...styles.approveBtn, opacity: busy ? 0.5 : 1 }}>
            {busy ? "…" : "Sign"}
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: 12,
    background: "#0b0f1c", color: "#e6eaf2",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif", boxSizing: "border-box",
  },
  card: {
    width: "100%", maxWidth: 360, background: "#101728", border: "1px solid #1f2a44", borderRadius: 12,
    padding: 16, display: "flex", flexDirection: "column", gap: 10,
  },
  title: { margin: 0, fontSize: 18, fontWeight: 600 },
  lede: { margin: 0, fontSize: 13, color: "#8a98b3" },
  originBox: { border: "1px solid #1f2a44", borderRadius: 10, padding: "10px 12px", background: "#0b0f1c" },
  originLabel: { fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "#8a98b3", marginBottom: 4 },
  originValue: { fontFamily: "ui-monospace, Menlo, monospace", fontSize: 13, wordBreak: "break-all" },
  msgLabel: { fontSize: 9, letterSpacing: "0.1em", textTransform: "uppercase", color: "#8a98b3", marginTop: 2 },
  message: {
    margin: 0, background: "#0b0f1c", border: "1px solid #1f2a44", borderRadius: 10, padding: 10,
    fontFamily: "ui-monospace, Menlo, monospace", fontSize: 12, color: "#cfd6e8",
    whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 160, overflowY: "auto",
  },
  warn: {
    margin: 0, fontSize: 12, color: "#ffcf7a", background: "rgba(255,176,32,.08)",
    border: "1px solid rgba(255,176,32,.3)", borderRadius: 8, padding: "8px 10px",
  },
  error: { color: "#ef6c6c", fontSize: 12, margin: 0 },
  actions: { display: "flex", gap: 8, marginTop: 4 },
  denyBtn: {
    flex: 1, background: "transparent", border: "1px solid #1f2a44", color: "#e6eaf2", borderRadius: 8,
    padding: "10px 12px", cursor: "pointer", fontSize: 13,
  },
  approveBtn: {
    flex: 2, background: "#56e2c1", border: "1px solid #56e2c1", color: "#0b0f1c", borderRadius: 8,
    padding: "10px 12px", cursor: "pointer", fontWeight: 600, fontSize: 13,
  },
};
