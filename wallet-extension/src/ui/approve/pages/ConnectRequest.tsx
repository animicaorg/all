import React from "react";
import Copy from "../../shared/components/Copy";

type Props = {
  origin: string;
  networkName?: string;
  accountAddress?: string;
};

const box: React.CSSProperties = {
  background: "var(--surface-2)",
  border: "1px solid var(--border-1)",
  borderRadius: 8,
  padding: "10px 12px",
};

const mono: React.CSSProperties = {
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  fontSize: 12,
  wordBreak: "break-word",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 16 }}>
      <h3 style={{ margin: "0 0 8px 0", fontSize: 13, letterSpacing: 0.2, color: "var(--text-2)" }}>{title}</h3>
      {children}
    </section>
  );
}

/**
 * ConnectRequest renders the informational body for a "connect" approval.
 * Buttons (Approve/Reject) are expected to be rendered by the parent (App.tsx).
 */
export default function ConnectRequest({ origin, networkName, accountAddress }: Props) {
  return (
    <>
      <Section title="Requesting site">
        <div style={{ ...box, display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{ display: "inline-block", width: 10, height: 10, borderRadius: "50%", background: "var(--accent-5)" }}
            aria-hidden
          />
          <code style={mono}>{origin || "unknown"}</code>
          {origin ? <Copy text={origin} title="Copy origin" /> : null}
        </div>
        <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "var(--text-3)" }}>
          Only approve if you trust this site. You can revoke access later in Settings.
        </p>
      </Section>

      <Section title="This site is requesting">
        <ul style={{ margin: 0, paddingLeft: 18 }}>
          <li>Read your public address</li>
          <li>Request signatures (with your approval)</li>
          <li>Request to send transactions (with your approval)</li>
        </ul>
      </Section>

      {(accountAddress || networkName) && (
        <Section title="Context to be shared on connect">
          <div style={{ display: "grid", gap: 8 }}>
            {accountAddress && (
              <div style={box}>
                <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Account</div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <code style={mono}>{accountAddress}</code>
                  <Copy text={accountAddress} title="Copy address" />
                </div>
              </div>
            )}
            {networkName && (
              <div style={box}>
                <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Network</div>
                <div style={{ fontSize: 13 }}>{networkName}</div>
              </div>
            )}
          </div>
        </Section>
      )}

      <Section title="Security tips">
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-3)" }}>
          <li>Check the browser address bar and make sure the domain looks correct.</li>
          <li>Never approve requests you don’t understand.</li>
          <li>You can disconnect any time from the wallet’s Settings.</li>
        </ul>
      </Section>
    </>
  );
}
