import * as React from "react";
import { downloadJson } from "../../utils/download";

/**
 * DeployProgress
 * Renders a clean status view for a deploy workflow:
 *  - status stepper
 *  - tx hash (copy / view on explorer)
 *  - receipt summary + JSON (download)
 *  - contract address (copy / view on explorer)
 */

export type DeployStatus =
  | "idle"
  | "building"
  | "signing"
  | "sending"
  | "pending"
  | "confirmed"
  | "failed";

export interface TxLog {
  address: string;
  topics: string[];
  data: string;
  index?: number;
}

export interface TxReceipt {
  status: "0x1" | "0x0" | 1 | 0 | boolean;
  blockHash?: string;
  blockNumber?: string | number;
  transactionHash?: string;
  gasUsed?: string | number;
  cumulativeGasUsed?: string | number;
  contractAddress?: string;
  effectiveGasPrice?: string | number;
  logs?: TxLog[];
  [k: string]: any; // allow extra fields from node
}

export interface DeployProgressProps {
  status: DeployStatus;
  txHash?: string | null;
  receipt?: TxReceipt | null;
  address?: string | null;
  chainId?: number | string;
  explorerBaseUrl?: string; // e.g. https://explorer.animica.xyz
  error?: string | null;
  onReset?: () => void;
}

/* -------------------------------- Component ------------------------------ */

export default function DeployProgress({
  status,
  txHash,
  receipt,
  address,
  explorerBaseUrl,
  error,
  onReset,
}: DeployProgressProps) {
  const [copied, setCopied] = React.useState<string | null>(null);

  const steps = useSteps(status);

  const txUrl =
    explorerBaseUrl && txHash ? joinUrl(explorerBaseUrl, "tx", txHash) : undefined;
  const addrUrl =
    explorerBaseUrl && (address || receipt?.contractAddress)
      ? joinUrl(explorerBaseUrl, "address", (address || receipt?.contractAddress)!)
      : undefined;

  const finalAddress = address || receipt?.contractAddress || null;

  return (
    <div className="space-y-4">
      {/* Stepper */}
      <section className="rounded border bg-white p-3">
        <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-2">
          Deployment Status
        </div>
        <Stepper steps={steps} />
        {status === "failed" && error && (
          <div className="mt-2 text-sm text-red-600 break-all">{error}</div>
        )}
      </section>

      {/* Transaction */}
      {txHash && (
        <section className="rounded border bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            Transaction
          </div>
          <Row
            label="Hash"
            value={shorten(txHash)}
            mono
            actions={
              <div className="flex gap-2">
                <Button
                  onClick={() => copyToClipboard(txHash, () => flash(setCopied, "hash"))}
                  title={copied === "hash" ? "Copied!" : "Copy hash"}
                >
                  {copied === "hash" ? "Copied" : "Copy"}
                </Button>
                {txUrl && (
                  <a
                    className="px-2 py-1 text-xs rounded border bg-white"
                    href={txUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="View on explorer"
                  >
                    View
                  </a>
                )}
              </div>
            }
          />
        </section>
      )}

      {/* Receipt */}
      {receipt && (
        <section className="rounded border bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)] mb-2">
            Receipt
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            <Row label="Status" value={receiptOk(receipt) ? "Success" : "Fail"} />
            <Row
              label="Block"
              value={receipt.blockNumber !== undefined ? String(receipt.blockNumber) : "—"}
            />
            <Row
              label="Gas Used"
              value={
                receipt.gasUsed !== undefined
                  ? formatNumberish(receipt.gasUsed)
                  : "—"
              }
            />
            <Row
              label="Eff. Gas Price"
              value={
                receipt.effectiveGasPrice !== undefined
                  ? formatNumberish(receipt.effectiveGasPrice)
                  : "—"
              }
            />
          </div>

          <details className="mt-2">
            <summary className="cursor-pointer text-sm">View JSON</summary>
            <pre className="mt-2 text-xs overflow-auto max-h-64 p-2 bg-[color:var(--muted-bg,#f9fafb)] rounded">
{JSON.stringify(receipt, null, 2)}
            </pre>
            <div className="mt-2">
              <Button onClick={() => downloadJson(receipt, "deploy-receipt.json")}>
                Download JSON
              </Button>
            </div>
          </details>
        </section>
      )}

      {/* Address */}
      {finalAddress && (
        <section className="rounded border bg-white p-3">
          <div className="text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
            Contract Address
          </div>
          <Row
            label="Address"
            value={shorten(finalAddress)}
            mono
            actions={
              <div className="flex gap-2">
                <Button
                  onClick={() =>
                    copyToClipboard(finalAddress, () => flash(setCopied, "addr"))
                  }
                  title={copied === "addr" ? "Copied!" : "Copy address"}
                >
                  {copied === "addr" ? "Copied" : "Copy"}
                </Button>
                {addrUrl && (
                  <a
                    className="px-2 py-1 text-xs rounded border bg-white"
                    href={addrUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="View on explorer"
                  >
                    View
                  </a>
                )}
              </div>
            }
          />
        </section>
      )}

      {/* Done / Reset */}
      {(status === "confirmed" || status === "failed") && onReset && (
        <div className="pt-2">
          <Button onClick={onReset}>New Deployment</Button>
        </div>
      )}
    </div>
  );
}

/* --------------------------------- UI ----------------------------------- */

function Stepper({ steps }: { steps: Array<{ label: string; done: boolean; active: boolean }> }) {
  return (
    <ol className="flex flex-wrap gap-3">
      {steps.map((s, i) => (
        <li key={i} className="flex items-center gap-2">
          <div
            className={[
              "w-5 h-5 rounded-full border flex items-center justify-center text-[10px]",
              s.done
                ? "bg-green-600 border-green-600 text-white"
                : s.active
                ? "bg-blue-600 border-blue-600 text-white"
                : "bg-white border-[color:var(--divider,#e5e7eb)] text-[color:var(--muted,#6b7280)]",
            ].join(" ")}
            aria-current={s.active ? "step" : undefined}
          >
            {s.done ? "✓" : i + 1}
          </div>
          <span
            className={[
              "text-sm",
              s.done || s.active ? "text-[color:var(--fg,#111827)]" : "text-[color:var(--muted,#6b7280)]",
            ].join(" ")}
          >
            {s.label}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Row({
  label,
  value,
  mono,
  actions,
}: {
  label: string;
  value: React.ReactNode;
  mono?: boolean;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3 py-1">
      <div className="min-w-[140px] text-xs uppercase tracking-wide text-[color:var(--muted,#6b7280)]">
        {label}
      </div>
      <div className={["flex-1 text-sm break-all", mono ? "font-mono" : ""].join(" ")}>{value}</div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  );
}

function Button({
  children,
  onClick,
  title,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  title?: string;
}) {
  return (
    <button
      type="button"
      className="px-2 py-1 text-xs rounded border bg-white"
      onClick={onClick}
      title={title}
    >
      {children}
    </button>
  );
}

/* ------------------------------- Helpers -------------------------------- */

function useSteps(status: DeployStatus) {
  const order: Array<[DeployStatus, string]> = [
    ["building", "Build Tx"],
    ["signing", "Sign"],
    ["sending", "Send"],
    ["pending", "Await Confirm"],
    ["confirmed", "Done"],
  ];
  const idx = indexForStatus(status);
  return order.map(([key, label], i) => ({
    label,
    done: i < idx || status === "confirmed",
    active: i === idx && status !== "confirmed" && status !== "failed",
  }));
}

function indexForStatus(s: DeployStatus): number {
  switch (s) {
    case "building":
      return 0;
    case "signing":
      return 1;
    case "sending":
      return 2;
    case "pending":
      return 3;
    case "confirmed":
      return 4;
    case "failed":
      // show where it failed by keeping the last active index
      return 3;
    default:
      return 0;
  }
}

function joinUrl(base: string, type: "tx" | "address", id: string) {
  const b = base.replace(/\/+$/, "");
  return `${b}/${type}/${encodeURIComponent(id)}`;
}

function shorten(v: string, head = 8, tail = 6) {
  if (!v) return "";
  if (v.length <= head + tail + 3) return v;
  return `${v.slice(0, head)}…${v.slice(-tail)}`;
}

function copyToClipboard(text: string, onDone?: () => void) {
  if (!text) return;
  navigator.clipboard
    ?.writeText(text)
    .then(() => onDone && onDone())
    .catch(() => {
      // fallback
      try {
        const ta = document.createElement("textarea");
        ta.value = text;
        ta.style.position = "fixed";
        ta.style.left = "-9999px";
        document.body.appendChild(ta);
        ta.focus();
        ta.select();
        document.execCommand("copy");
        document.body.removeChild(ta);
        onDone && onDone();
      } catch {
        /* ignore */
      }
    });
}

function flash(setter: React.Dispatch<React.SetStateAction<string | null>>, key: string, ms = 1200) {
  setter(key);
  setTimeout(() => setter((k) => (k === key ? null : k)), ms);
}

function receiptOk(rcpt: TxReceipt): boolean {
  const s = rcpt?.status;
  if (s === true || s === 1 || s === "0x1") return true;
  if (s === false || s === 0 || s === "0x0") return false;
  // unknown is treated as pending / unknown
  return false;
}

function formatNumberish(v: string | number): string {
  if (typeof v === "number") return v.toString();
  // hex?
  if (/^0x[0-9a-fA-F]+$/.test(v)) {
    try {
      return BigInt(v).toString();
    } catch {
      return v;
    }
  }
  return v;
}
