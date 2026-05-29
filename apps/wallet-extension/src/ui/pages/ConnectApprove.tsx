import React, { useEffect, useState } from "react";

interface PendingAccount {
  address: string;
  label: string;
}

interface PendingConnect {
  requestId: string;
  origin: string;
  createdAt: number;
  accounts: PendingAccount[];
}

interface Props {
  pending: PendingConnect;
  onResolved: () => void;
}

export default function ConnectApprove({ pending, onResolved }: Props) {
  // Default selection: the first wallet only. Users opt-in to share more.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(pending.accounts.length > 0 ? [pending.accounts[0].address] : []),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(addr: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(addr) ? next.delete(addr) : next.add(addr);
      return next;
    });
  }

  async function respond(approved: boolean) {
    setBusy(true);
    setError(null);
    try {
      const res = await chrome.runtime.sendMessage({
        method: "wallet_connectRespond",
        params: {
          requestId: pending.requestId,
          approved,
          accounts: approved ? Array.from(selected) : [],
        },
      });
      if (res?.error) throw new Error(res.error);
      onResolved();
    } catch (e: any) {
      setError(e?.message || "Could not respond.");
      setBusy(false);
    }
  }

  const canApprove = !busy && selected.size > 0;

  // Origin display: strip protocol, keep host.
  const host = (() => {
    try { return new URL(pending.origin).host; } catch { return pending.origin; }
  })();

  return (
    <div style={styles.page}>
      <div style={styles.card}>
        <h2 style={styles.title}>Connect request</h2>
        <p style={styles.lede}>
          A site wants to see your Animica accounts.
        </p>

        <div style={styles.originBox}>
          <div style={styles.originLabel}>SITE</div>
          <div style={styles.originValue}>{host}</div>
        </div>

        <div style={styles.scopeBox}>
          <div style={styles.scopeRow}>
            <span style={styles.scopeBullet}>•</span>
            <span>See addresses you select below</span>
          </div>
          <div style={styles.scopeRow}>
            <span style={styles.scopeBullet}>•</span>
            <span>Request you to sign transactions (asked again, not auto-approved)</span>
          </div>
        </div>

        <div style={styles.accountsHeader}>Share these accounts</div>
        {pending.accounts.length === 0 ? (
          <p style={{ color: "#f39", fontSize: 13 }}>You have no wallets yet.</p>
        ) : (
          <ul style={styles.accountsList}>
            {pending.accounts.map((a) => (
              <li key={a.address}>
                <label style={styles.accountRow}>
                  <input
                    type="checkbox"
                    checked={selected.has(a.address)}
                    onChange={() => toggle(a.address)}
                    style={styles.checkbox}
                  />
                  <span style={styles.accountLabel}>{a.label}</span>
                  <span style={styles.accountAddr}>
                    {a.address.slice(0, 12)}…{a.address.slice(-6)}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}

        {error ? <p style={styles.error}>{error}</p> : null}

        <div style={styles.actions}>
          <button
            type="button"
            onClick={() => respond(false)}
            disabled={busy}
            style={styles.denyBtn}
          >
            Deny
          </button>
          <button
            type="button"
            onClick={() => respond(true)}
            disabled={!canApprove}
            style={{ ...styles.approveBtn, opacity: canApprove ? 1 : 0.5 }}
          >
            {busy ? "…" : "Connect"}
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  page: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 12,
    background: "#0b0f1c",
    color: "#e6eaf2",
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    boxSizing: "border-box",
  },
  card: {
    width: "100%",
    maxWidth: 360,
    background: "#101728",
    border: "1px solid #1f2a44",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  title: { margin: 0, fontSize: 18, fontWeight: 600 },
  lede: { margin: 0, fontSize: 13, color: "#8a98b3" },
  originBox: {
    border: "1px solid #1f2a44",
    borderRadius: 10,
    padding: "10px 12px",
    background: "#0b0f1c",
  },
  originLabel: {
    fontSize: 9,
    letterSpacing: "0.1em",
    textTransform: "uppercase",
    color: "#8a98b3",
    marginBottom: 4,
  },
  originValue: { fontFamily: "ui-monospace, Menlo, monospace", fontSize: 13, wordBreak: "break-all" },
  scopeBox: {
    background: "#0b0f1c",
    border: "1px solid #1f2a44",
    borderRadius: 10,
    padding: 10,
    fontSize: 12,
    color: "#cfd6e8",
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  scopeRow: { display: "flex", gap: 8, alignItems: "flex-start" },
  scopeBullet: { color: "#56e2c1", flexShrink: 0 },
  accountsHeader: {
    fontSize: 11,
    letterSpacing: "0.08em",
    textTransform: "uppercase",
    color: "#8a98b3",
    marginTop: 4,
  },
  accountsList: { listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 4 },
  accountRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 10px",
    border: "1px solid #1f2a44",
    borderRadius: 8,
    cursor: "pointer",
    background: "#0b0f1c",
  },
  checkbox: { accentColor: "#56e2c1" },
  accountLabel: { fontSize: 13, fontWeight: 500 },
  accountAddr: { marginLeft: "auto", fontFamily: "ui-monospace, Menlo, monospace", fontSize: 11, color: "#8a98b3" },
  error: { color: "#ef6c6c", fontSize: 12, margin: 0 },
  actions: { display: "flex", gap: 8, marginTop: 4 },
  denyBtn: {
    flex: 1,
    background: "transparent",
    border: "1px solid #1f2a44",
    color: "#e6eaf2",
    borderRadius: 8,
    padding: "10px 12px",
    cursor: "pointer",
    fontSize: 13,
  },
  approveBtn: {
    flex: 2,
    background: "#56e2c1",
    border: "1px solid #56e2c1",
    color: "#0b0f1c",
    borderRadius: 8,
    padding: "10px 12px",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: 13,
  },
};
