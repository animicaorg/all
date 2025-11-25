import React, { useMemo, useState } from "react";
import Copy from "../../shared/components/Copy";

type Props = {
  /** Requesting site origin (e.g. https://app.example.com) */
  origin: string;
  /** Bech32m account address (anim1...) */
  accountAddress?: string;
  /** 0x-prefixed hex payload to be signed (domain-separated sign bytes) */
  payloadHex: string;
  /** Optional pre-decoded UTF-8 preview; if absent we'll try to decode from hex */
  previewUtf8?: string;
  /** Optional signing context */
  algorithm?: "dilithium3" | "sphincs-shake-128s";
  domainTag?: string; // e.g., "Animica-SignBytes-v1"
  chainId?: number;
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

function hexLen(hex: string): number {
  const h = hex.startsWith("0x") ? hex.slice(2) : hex;
  return h.length % 2 === 0 ? h.length / 2 : Math.floor(h.length / 2);
}

function tryUtf8FromHex(hex: string): string | null {
  try {
    const h = hex.startsWith("0x") ? hex.slice(2) : hex;
    if (h.length % 2 !== 0) return null;
    const bytes = new Uint8Array(h.length / 2);
    for (let i = 0; i < h.length; i += 2) bytes[i / 2] = parseInt(h.slice(i, i + 2), 16);
    const dec = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
    // Heuristic: treat as printable if most chars are not control chars
    const printable = dec.replace(/[\x00-\x08\x0E-\x1F\x7F]/g, "");
    return printable.length / Math.max(dec.length, 1) > 0.9 ? dec : null;
  } catch {
    return null;
  }
}

/**
 * SignRequest renders the informational body for a "sign" approval.
 * Parent component (App.tsx) should render Approve/Reject buttons.
 */
export default function SignRequest({
  origin,
  accountAddress,
  payloadHex,
  previewUtf8,
  algorithm,
  domainTag,
  chainId,
}: Props) {
  const [showHex, setShowHex] = useState(false);
  const bytesLen = useMemo(() => hexLen(payloadHex), [payloadHex]);

  const utf8 = useMemo(() => {
    if (previewUtf8 && previewUtf8.length > 0) return previewUtf8;
    return tryUtf8FromHex(payloadHex) ?? "";
  }, [previewUtf8, payloadHex]);

  const hasUtf8 = utf8.length > 0;

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
          Only sign if you trust this site and understand the request.
        </p>
      </Section>

      <Section title="What you are signing">
        <div style={{ ...box }}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", marginBottom: 8 }}>
            {domainTag && (
              <span
                style={{
                  fontSize: 11,
                  padding: "2px 6px",
                  borderRadius: 6,
                  background: "var(--surface-3)",
                  border: "1px solid var(--border-1)",
                }}
                title="Domain separation tag"
              >
                {domainTag}
              </span>
            )}
            {typeof chainId === "number" && (
              <span style={{ fontSize: 11, color: "var(--text-3)" }} title="Chain ID">
                chainId: {chainId}
              </span>
            )}
            <span style={{ fontSize: 11, color: "var(--text-3)" }} title="Payload size">
              {bytesLen} bytes
            </span>
          </div>

          {hasUtf8 && !showHex && (
            <div>
              <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>UTF-8 preview</div>
              <pre style={{ ...mono, margin: 0, whiteSpace: "pre-wrap" }}>{utf8}</pre>
            </div>
          )}

          {(showHex || !hasUtf8) && (
            <div>
              <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Raw hex</div>
              <pre style={{ ...mono, margin: 0, userSelect: "text" }}>{payloadHex}</pre>
            </div>
          )}

          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
            {hasUtf8 && (
              <button
                onClick={() => setShowHex((v) => !v)}
                style={{
                  fontSize: 12,
                  padding: "6px 10px",
                  borderRadius: 6,
                  border: "1px solid var(--border-1)",
                  background: "var(--surface-1)",
                  cursor: "pointer",
                }}
              >
                {showHex ? "Show UTF-8 preview" : "Show raw hex"}
              </button>
            )}
            <Copy text={showHex || !hasUtf8 ? payloadHex : utf8} title="Copy content" />
          </div>
        </div>

        <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "var(--text-3)" }}>
          The wallet will apply domain separation before signing to prevent cross-context replay.
        </p>
      </Section>

      <Section title="Signing context">
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

          <div style={box}>
            <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Signature algorithm</div>
            <div style={{ fontSize: 13 }}>{algorithm ?? "auto"}</div>
          </div>
        </div>
      </Section>

      <Section title="Security tips">
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-3)" }}>
          <li>Never sign messages you do not understand.</li>
          <li>Phishing sites may ask you to sign to “verify” your wallet — be cautious.</li>
          <li>Check the domain in the address bar and compare it to the one shown above.</li>
          <li>Signing does not move funds, but it can authorize actions on connected dapps.</li>
        </ul>
      </Section>
    </>
  );
}
