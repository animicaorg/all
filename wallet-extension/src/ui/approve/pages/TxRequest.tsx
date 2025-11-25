import React, { useMemo, useState } from "react";
import Copy from "../../shared/components/Copy";

type SimulationLog = { event?: string; args?: Record<string, unknown> };
type SimulationOutcome =
  | { status: "idle" }
  | {
      status: "success" | "revert" | "error";
      gasUsed?: number;
      reason?: string;
      logs?: SimulationLog[];
      returnsHex?: string;
      warnings?: string[];
    };

export type TxPreview = {
  to?: string | null; // undefined/null => contract creation
  value?: string | number | bigint; // base units (atoms)
  dataHex?: string; // 0x...
  nonce?: number;
  gasLimit?: number;
  maxFeePerGas?: string; // base units
  maxPriorityFeePerGas?: string; // base units
};

type Props = {
  /** Requesting site origin */
  origin: string;
  /** Selected account (sender) */
  accountAddress?: string;
  /** Target chain */
  chainId: number;
  /** The transaction fields to display */
  tx: TxPreview;
  /** Optional simulation outcome (background runs this before prompting) */
  simulation?: SimulationOutcome;
  /** Human hints (resolved names, known contract) */
  hints?: {
    toLabel?: string; // ENS-like label or contract name
    riskNotes?: string[];
  };
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

function formatUnits(v: string | number | bigint | undefined | null): string {
  if (v === undefined || v === null) return "0";
  try {
    const bi = typeof v === "bigint" ? v : BigInt(v as any);
    return `${bi.toString()} atoms`;
  } catch {
    return String(v);
  }
}

function short(addr?: string | null): string {
  if (!addr) return "";
  return addr.length > 12 ? `${addr.slice(0, 8)}…${addr.slice(-6)}` : addr;
}

function hexLen(hex?: string): number {
  if (!hex) return 0;
  const h = hex.startsWith("0x") ? hex.slice(2) : hex;
  return Math.floor(h.length / 2);
}

/**
 * TxRequest renders the informational body for a "send transaction" approval.
 * Parent (App.tsx) is responsible for rendering Approve/Reject buttons and wiring actions.
 */
export default function TxRequest({ origin, accountAddress, chainId, tx, simulation, hints }: Props) {
  const [showData, setShowData] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const kind = useMemo<"create" | "transfer" | "call" | "callWithValue">(() => {
    const hasTo = !!tx.to;
    const hasData = !!tx.dataHex && hexLen(tx.dataHex) > 0;
    const hasValue = !!tx.value && formatUnits(tx.value) !== "0 atoms";
    if (!hasTo) return "create";
    if (hasData && hasValue) return "callWithValue";
    if (hasData) return "call";
    return "transfer";
  }, [tx.to, tx.dataHex, tx.value]);

  const sim = simulation ?? { status: "idle" as const };

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
          Review the transaction details below. Only continue if you trust this site.
        </p>
      </Section>

      <Section title="Summary">
        <div style={{ ...box, display: "grid", gap: 10 }}>
          <Row label="Action">
            <span style={{ textTransform: "capitalize" }}>
              {kind === "create"
                ? "Deploy contract"
                : kind === "transfer"
                ? "Transfer"
                : kind === "callWithValue"
                ? "Contract call (with value)"
                : "Contract call"}
            </span>
          </Row>

          <Row label="From">
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <code style={mono}>{accountAddress ?? "(not set)"}</code>
              {accountAddress ? <Copy text={accountAddress} title="Copy sender" /> : null}
            </div>
          </Row>

          <Row label={kind === "create" ? "To" : "To"}>
            {kind === "create" ? (
              <span>(contract creation)</span>
            ) : (
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <code style={mono}>{tx.to ?? "(missing)"}</code>
                {tx.to ? <Copy text={tx.to} title="Copy recipient" /> : null}
                {hints?.toLabel ? (
                  <span style={{ fontSize: 11, color: "var(--text-3)" }}>• {hints.toLabel}</span>
                ) : null}
              </div>
            )}
          </Row>

          <Row label="Amount">
            <span>{formatUnits(tx.value)}</span>
          </Row>

          <Row label="Network">
            <span>chainId {chainId}</span>
          </Row>
        </div>
      </Section>

      <Section title="Data">
        <div style={{ ...box }}>
          {tx.dataHex && hexLen(tx.dataHex) > 0 ? (
            <>
              <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-3)" }}>{hexLen(tx.dataHex)} bytes</span>
                <button
                  onClick={() => setShowData((v) => !v)}
                  style={{
                    fontSize: 12,
                    padding: "6px 10px",
                    borderRadius: 6,
                    border: "1px solid var(--border-1)",
                    background: "var(--surface-1)",
                    cursor: "pointer",
                  }}
                >
                  {showData ? "Hide" : "Show"}
                </button>
                <Copy text={tx.dataHex} title="Copy data hex" />
              </div>
              {showData && <pre style={{ ...mono, margin: 0 }}>{tx.dataHex}</pre>}
            </>
          ) : (
            <span style={{ fontSize: 12, color: "var(--text-3)" }}>No calldata</span>
          )}
        </div>
      </Section>

      <Section title="Fees (estimated)">
        <div style={{ ...box, display: "grid", gap: 6 }}>
          <Row label="Gas limit">
            <span>{tx.gasLimit ?? "—"}</span>
          </Row>
          <Row label="Max fee / gas">
            <span>{tx.maxFeePerGas ? `${tx.maxFeePerGas} atoms` : "—"}</span>
          </Row>
          <Row label="Priority fee / gas">
            <span>{tx.maxPriorityFeePerGas ? `${tx.maxPriorityFeePerGas} atoms` : "—"}</span>
          </Row>
          <Row label="Nonce">
            <span>{tx.nonce ?? "auto"}</span>
          </Row>
        </div>
        <details style={{ marginTop: 8 }} open={showAdvanced} onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}>
          <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--text-2)" }}>Advanced</summary>
          <ul style={{ margin: "6px 0 0 18px", fontSize: 12, color: "var(--text-3)" }}>
            <li>Final fees depend on actual gas used and base fee at inclusion.</li>
            <li>Large calldata or complex contract logic increases gas usage.</li>
          </ul>
        </details>
      </Section>

      <Section title="Simulation">
        <div style={{ ...box, display: "grid", gap: 8 }}>
          {sim.status === "idle" && <span style={{ fontSize: 12, color: "var(--text-3)" }}>No simulation info.</span>}
          {sim.status !== "idle" && (
            <>
              <Row label="Outcome">
                <span
                  style={{
                    fontWeight: 600,
                    color:
                      sim.status === "success" ? "var(--green-9)" : sim.status === "revert" ? "var(--amber-9)" : "var(--red-9)",
                  }}
                >
                  {sim.status}
                </span>
              </Row>
              <Row label="Gas used">
                <span>{sim.gasUsed ?? "—"}</span>
              </Row>
              {sim.reason && (
                <Row label="Reason">
                  <span style={{ color: "var(--text-3)" }}>{sim.reason}</span>
                </Row>
              )}
              {sim.warnings && sim.warnings.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Warnings</div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
                    {sim.warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
              {sim.logs && sim.logs.length > 0 && (
                <div>
                  <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>Emitted events</div>
                  <div style={{ ...mono, fontSize: 11 }}>
                    {sim.logs.slice(0, 6).map((l, i) => (
                      <div key={i} style={{ marginBottom: 4 }}>
                        {l.event ?? "Event"} {JSON.stringify(l.args ?? {}, null, 0)}
                      </div>
                    ))}
                    {sim.logs.length > 6 ? <div>… {sim.logs.length - 6} more</div> : null}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
        <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "var(--text-3)" }}>
          Simulation is a best-effort preview. Final execution may differ due to state changes or reverts.
        </p>
      </Section>

      {(hints?.riskNotes?.length ?? 0) > 0 && (
        <Section title="Risk checks">
          <div style={{ ...box }}>
            <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12 }}>
              {hints!.riskNotes!.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          </div>
        </Section>
      )}

      <Section title="Security tips">
        <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-3)" }}>
          <li>Verify the recipient and the amount.</li>
          <li>Be cautious when sending value to new or unverified contracts ({short(tx.to)}).</li>
          <li>If simulation shows a <strong>revert</strong> or <strong>error</strong>, do not approve.</li>
        </ul>
      </Section>
    </>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "140px 1fr", alignItems: "center", gap: 10 }}>
      <div style={{ fontSize: 12, color: "var(--text-3)" }}>{label}</div>
      <div>{children}</div>
    </div>
  );
}
