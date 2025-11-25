import * as React from "react";

/**
 * VerifyStatus
 * Presentational component to display a verification job's lifecycle,
 * verdict, and details. Fully controlled via props and safe to reuse
 * across pages (e.g. VerifyPage, modals, sidebars).
 */

export type VerifyStatusState = "idle" | "queued" | "running" | "succeeded" | "failed";

export interface VerifyResult {
  jobId: string;
  targetType: "address" | "txHash";
  target: string;
  compiledCodeHash: string; // 0x…
  onChainCodeHash?: string; // 0x…
  match: boolean;
  manifestDigest?: string; // 0x…
  diagnostics?: Array<{ level: "info" | "warning" | "error"; msg: string }>;
  createdAt?: string;
  completedAt?: string;
}

export interface VerifyStatusProps {
  jobId?: string | null;
  status: VerifyStatusState;
  result?: VerifyResult | null;
  error?: string | null;
  compact?: boolean;
  onDownloadJson?: (res: VerifyResult) => void;
  onReset?: () => void;
}

/** A compact badge showing the verdict if available */
function VerdictBadge({ result }: { result?: VerifyResult | null }) {
  if (!result) return null;
  const ok = result.match;
  const cls = ok ? "bg-green-600" : "bg-yellow-500";
  const label = ok ? "MATCH" : "MISMATCH";
  return (
    <span className={`text-white text-[11px] px-2 py-1 rounded ${cls}`} title="Verification verdict">
      {label}
    </span>
  );
}

export function VerifyStatus({
  jobId,
  status,
  result,
  error,
  compact,
  onDownloadJson,
  onReset,
}: VerifyStatusProps) {
  const verdict =
    status === "succeeded" && result
      ? { label: result.match ? "MATCH" : "MISMATCH", className: result.match ? "bg-green-600" : "bg-yellow-500" }
      : null;

  return (
    <section className="rounded border bg-white p-4 space-y-4" aria-live="polite">
      <header className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold">Verification Status</h2>
          {!compact && <VerdictBadge result={result || null} />}
        </div>
        {jobId ? (
          <JobId jobId={jobId} />
        ) : null}
      </header>

      <Stepper status={status} />

      {error && (
        <div role="alert" className="text-sm text-red-700 bg-red-50 border border-red-200 rounded p-2 break-all">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <KV label="Target" value={`${result.targetType}:${shorten(result.target)}`} />
            {verdict && (
              <KV
                label="Verdict"
                value={<span className={`text-white text-[11px] px-2 py-1 rounded ${verdict.className}`}>{verdict.label}</span>}
              />
            )}
            <KV label="Compiled Code Hash" value={shorten(result.compiledCodeHash)} mono />
            <KV label="On-chain Code Hash" value={result.onChainCodeHash ? shorten(result.onChainCodeHash) : "—"} mono />
            {result.manifestDigest && <KV label="Manifest Digest" value={shorten(result.manifestDigest)} mono />}
            {result.createdAt && <KV label="Created" value={formatDate(result.createdAt)} />}
            {result.completedAt && <KV label="Completed" value={formatDate(result.completedAt)} />}
          </div>

          {result.diagnostics && result.diagnostics.length > 0 && (
            <Diagnostics items={result.diagnostics} />
          )}

          <div className="flex items-center gap-2 pt-1">
            {onDownloadJson && result && (
              <button
                type="button"
                className="px-3 py-2 rounded border text-sm"
                onClick={() => onDownloadJson(result)}
              >
                Download JSON
              </button>
            )}
            {onReset && (
              <button type="button" className="px-3 py-2 rounded border text-sm" onClick={onReset}>
                Reset
              </button>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

/* --------------------------------- Bits --------------------------------- */

function JobId({ jobId }: { jobId: string }) {
  const [copied, setCopied] = React.useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(jobId);
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // ignore
    }
  }
  return (
    <div className="flex items-center gap-2">
      <code className="text-[11px] px-2 py-1 rounded bg-[color:var(--muted-bg,#f3f4f6)] break-all">{jobId}</code>
      <button
        type="button"
        onClick={copy}
        className="px-2 py-1 text-xs rounded border"
        title="Copy job id"
        aria-label="Copy job id"
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function KV({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start gap-2">
      <div className="min-w-[160px] text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className={["flex-1 text-sm break-all", mono ? "font-mono" : ""].join(" ")}>{value}</div>
    </div>
  );
}

function Stepper({ status }: { status: VerifyStatusState }) {
  const order: Array<[VerifyStatusState, string]> = [
    ["queued", "Queued"],
    ["running", "Compiling/Comparing"],
    ["succeeded", "Done"],
  ];
  const idx = indexFor(status);
  return (
    <ol className="flex flex-wrap gap-3" aria-label="Verification progress">
      {order.map(([st, label], i) => {
        const done = i < idx || status === "succeeded";
        const active = st === status && status !== "succeeded" && status !== "failed";
        return (
          <li key={st} className="flex items-center gap-2">
            <div
              className={[
                "w-5 h-5 rounded-full border flex items-center justify-center text-[10px]",
                done
                  ? "bg-green-600 border-green-600 text-white"
                  : active
                  ? "bg-blue-600 border-blue-600 text-white"
                  : "bg-white border-[color:var(--divider,#e5e7eb)] text-[color:var(--muted,#6b7280)]",
              ].join(" ")}
              aria-current={active ? "step" : undefined}
            >
              {done ? "✓" : i + 1}
            </div>
            <span
              className={[
                "text-sm",
                done || active ? "text-[color:var(--fg,#111827)]" : "text-[color:var(--muted,#6b7280)]",
              ].join(" ")}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

function indexFor(s: VerifyStatusState) {
  switch (s) {
    case "queued":
      return 0;
    case "running":
      return 1;
    case "succeeded":
      return 2;
    case "failed":
      return 1;
    default:
      return 0;
  }
}

function Diagnostics({ items }: { items: Array<{ level: "info" | "warning" | "error"; msg: string }> }) {
  return (
    <details className="mt-1">
      <summary className="cursor-pointer text-sm">Diagnostics</summary>
      <ul className="mt-2 text-sm list-disc pl-5">
        {items.map((d, i) => (
          <li
            key={i}
            className={
              d.level === "error" ? "text-red-700" : d.level === "warning" ? "text-yellow-700" : "text-[color:var(--fg,#111827)]"
            }
          >
            [{d.level}] {d.msg}
          </li>
        ))}
      </ul>
    </details>
  );
}

function shorten(v: string, head = 10, tail = 8) {
  if (!v) return "";
  if (v.length <= head + tail + 3) return v;
  return `${v.slice(0, head)}…${v.slice(-tail)}`;
}

function formatDate(iso: string) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

export default VerifyStatus;
