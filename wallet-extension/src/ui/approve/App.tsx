import React, { useEffect, useMemo, useState } from "react";
import type { InitialApproveState, ApproveKind } from "./main";
import Button from "../shared/components/Button";
import Copy from "../shared/components/Copy";
import "../shared/theme.css";

type Props = {
  initial: InitialApproveState;
};

type AnyRecord = Record<string, unknown>;

type ApproveRequest =
  | {
      id: string;
      kind: ApproveKind;
      origin: string;
      // generic payload fields (background can send any)
      method?: string;
      params?: AnyRecord | unknown[];
      message?: string;
      signBytesHex?: string;
      tx?: AnyRecord;
      meta?: AnyRecord;
    }
  | null;

function useChromeMessage<TRes = unknown, TReq = unknown>(type: string) {
  return (payload?: TReq) =>
    new Promise<TRes>((resolve, reject) => {
      try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        (chrome as any)?.runtime?.sendMessage?.({ type, ...(payload as any) }, (res: unknown) => {
          const err = (chrome as any)?.runtime?.lastError;
          if (err) return reject(new Error(err.message || String(err)));
          resolve(res as TRes);
        });
      } catch (e) {
        reject(e);
      }
    });
}

const requestStyles: React.CSSProperties = {
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace",
  whiteSpace: "pre-wrap",
  wordBreak: "break-word",
  background: "var(--surface-2)",
  border: "1px solid var(--border-1)",
  borderRadius: 8,
  padding: 12,
  maxHeight: 200,
  overflow: "auto",
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 16 }}>
      <h3 style={{ margin: "0 0 8px 0", fontSize: 13, letterSpacing: 0.2, color: "var(--text-2)" }}>{title}</h3>
      {children}
    </section>
  );
}

export default function App({ initial }: Props) {
  const [loading, setLoading] = useState(true);
  const [req, setReq] = useState<ApproveRequest>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const fetchReq = useChromeMessage<{ ok: boolean; request?: ApproveRequest; error?: string }>("approve:get");
  const sendAccept = useChromeMessage<{ ok: boolean; error?: string }, { requestId?: string }>("approve:accept");
  const sendReject = useChromeMessage<{ ok: boolean; error?: string }, { requestId?: string; reason?: string }>(
    "approve:reject"
  );

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const res = await fetchReq({ requestId: initial.requestId });
        if (cancelled) return;
        if (res?.ok && res.request) {
          setReq(res.request);
        } else {
          // Fallback: build minimal view from query params if background didn't reply
          setReq({
            id: initial.requestId || "unknown",
            kind: initial.kind,
            origin: initial.origin || "unknown",
          });
          if (res?.error) setError(res.error);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initial.kind, initial.origin, initial.requestId, fetchReq]);

  const title = useMemo(() => {
    if (!req) return "Request";
    switch (req.kind) {
      case "connect":
        return "Connection request";
      case "sign":
        return "Signature request";
      case "tx":
        return "Transaction request";
      default:
        return "Request";
    }
  }, [req]);

  async function onApprove() {
    setLoading(true);
    setError(null);
    try {
      await sendAccept({ requestId: req?.id || initial.requestId });
      // Give background a beat to resolve & close us.
      setTimeout(() => window.close(), 50);
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  }

  async function onReject() {
    setLoading(true);
    setError(null);
    try {
      await sendReject({ requestId: req?.id || initial.requestId, reason: "UserRejected" });
      setTimeout(() => window.close(), 50);
    } catch (e) {
      setError((e as Error).message);
      setLoading(false);
    }
  }

  function renderBody() {
    if (!req) return null;

    return (
      <>
        <Section title="Requesting site">
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              background: "var(--surface-2)",
              border: "1px solid var(--border-1)",
              borderRadius: 8,
              padding: "10px 12px",
            }}
          >
            <span
              style={{
                display: "inline-block",
                width: 10,
                height: 10,
                borderRadius: "50%",
                background: "var(--accent-5)",
              }}
              aria-hidden
            />
            <code style={{ fontSize: 12, color: "var(--text-1)" }}>{req.origin || "unknown"}</code>
            {req.origin && <Copy text={req.origin} title="Copy origin" />}
          </div>
          <p style={{ margin: "8px 0 0 0", fontSize: 12, color: "var(--text-3)" }}>
            Only approve if you trust this site. You can revoke this permission later in Settings.
          </p>
        </Section>

        {req.kind === "connect" && (
          <Section title="This site is requesting">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              <li>Read your public address</li>
              <li>Request signatures and send transactions (with your approval)</li>
            </ul>
          </Section>
        )}

        {req.kind === "sign" && (
          <>
            {req.message && (
              <Section title="Message to sign">
                <div style={requestStyles}>{req.message}</div>
              </Section>
            )}
            {req.signBytesHex && (
              <Section title="Sign bytes (hex)">
                <div style={requestStyles}>
                  <code>{previewHex(req.signBytesHex, expanded)}</code>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <Button kind="ghost" onClick={() => setExpanded((v) => !v)}>
                    {expanded ? "Collapse" : "Expand"}
                  </Button>
                  <Copy text={req.signBytesHex} title="Copy hex" />
                </div>
              </Section>
            )}
            {req.method && (
              <Section title="Method">
                <code style={{ fontSize: 12 }}>{req.method}</code>
              </Section>
            )}
          </>
        )}

        {req.kind === "tx" && (
          <>
            {req.tx && (
              <Section title="Transaction">
                <div style={requestStyles}>
                  <pre style={{ margin: 0, fontSize: 12 }}>
                    {JSON.stringify(req.tx, null, 2).slice(0, expanded ? undefined : 1200)}
                    {!expanded && JSON.stringify(req.tx, null, 2).length > 1200 ? "… (expand to view more)" : ""}
                  </pre>
                </div>
                <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                  <Button kind="ghost" onClick={() => setExpanded((v) => !v)}>
                    {expanded ? "Collapse" : "Expand"}
                  </Button>
                  <Copy text={JSON.stringify(req.tx, null, 2)} title="Copy JSON" />
                </div>
              </Section>
            )}
            {req.method && (
              <Section title="Method">
                <code style={{ fontSize: 12 }}>{req.method}</code>
              </Section>
            )}
          </>
        )}

        {(req.params || req.meta) && (
          <Section title="Details">
            <div style={requestStyles}>
              <pre style={{ margin: 0, fontSize: 12 }}>
                {JSON.stringify({ params: req.params ?? null, meta: req.meta ?? null }, null, 2)}
              </pre>
            </div>
          </Section>
        )}
      </>
    );
  }

  return (
    <div
      style={{
        minWidth: 380,
        maxWidth: 520,
        padding: 16,
        boxSizing: "border-box",
        color: "var(--text-1)",
      }}
    >
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 12,
          marginBottom: 6,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16 }}>{title}</h2>
        <span
          style={{
            fontSize: 11,
            color: "var(--text-3)",
            border: "1px solid var(--border-1)",
            padding: "2px 6px",
            borderRadius: 6,
          }}
        >
          {req?.id ? short(req.id) : initial.requestId ? short(initial.requestId) : "—"}
        </span>
      </header>

      {error && (
        <div
          role="alert"
          style={{
            background: "var(--danger-1)",
            color: "var(--danger-11)",
            border: "1px solid var(--danger-6)",
            borderRadius: 8,
            padding: 10,
            marginTop: 8,
            fontSize: 12,
          }}
        >
          {error}
        </div>
      )}

      <main style={{ opacity: loading ? 0.6 : 1, pointerEvents: loading ? "none" : "auto" }}>{renderBody()}</main>

      <footer
        style={{
          display: "flex",
          gap: 12,
          justifyContent: "flex-end",
          marginTop: 18,
          borderTop: "1px solid var(--border-1)",
          paddingTop: 12,
        }}
      >
        <Button kind="secondary" onClick={onReject} disabled={loading}>
          Reject
        </Button>
        <Button onClick={onApprove} disabled={loading}>
          Approve
        </Button>
      </footer>
    </div>
  );
}

function short(id: string, n = 6) {
  if (id.length <= n * 2 + 1) return id;
  return `${id.slice(0, n)}…${id.slice(-n)}`;
}

function previewHex(hex: string, expanded: boolean) {
  const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
  if (expanded || clean.length <= 256) return `0x${clean}`;
  return `0x${clean.slice(0, 256)}…`;
}
