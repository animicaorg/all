import { useState } from "react";
import { useEnaStore } from "@/state/ena";
import { useWalletStore } from "@/state/wallet";

const WEB_WALLET = "https://wallet.animica.org";

function fmt(n: number, max = 4): string {
  if (!Number.isFinite(n)) return "0";
  return Number(n.toFixed(max)).toString();
}

// Manual budget funding — for the web wallet (or any wallet that can't sign in
// the Studio). The user sends ANM to the treasury themselves, then pastes the
// tx id; the broker verifies it on-chain and credits the budget.
export function ManualFund() {
  const { treasury, cap, balanceAnm, perCallAnm, depositing, error, submitDeposit, setManualOpen } =
    useEnaStore();
  const walletKind = useWalletStore((s) => s.kind);
  const [txid, setTxid] = useState("");
  const [copied, setCopied] = useState(false);

  const amount = Math.max(perCallAnm || 0, Number((cap - balanceAnm).toFixed(9)));

  function copy() {
    navigator.clipboard?.writeText(treasury).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => {},
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4"
      onClick={() => setManualOpen(false)}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="safe-b w-full max-w-sm rounded-t-2xl border border-border bg-surface p-4 shadow-2xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">Fund your ENA budget</h2>
          <button className="text-muted hover:text-fg" onClick={() => setManualOpen(false)} aria-label="Close">
            <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M6 6l12 12M18 6 6 18" />
            </svg>
          </button>
        </div>

        {walletKind === "web" && (
          <p className="mb-3 rounded-lg border border-warn/30 bg-warn/10 px-3 py-2 text-xs leading-snug text-warn">
            The web wallet can only share your address — it can't sign here. For one-tap
            funding (confirm in wallet, auto-credit), open the Studio in the Animica app's
            browser or use the extension. Otherwise, send manually below.
          </p>
        )}
        <p className="mb-3 text-sm text-muted">
          Send{" "}
          <span className="font-medium text-fg">{fmt(amount)} ANM</span> to the Studio treasury from your
          wallet, then paste the transaction ID to credit your budget.
        </p>

        <label className="mb-1 block text-xs font-medium text-muted">Treasury address</label>
        <div className="mb-2 flex items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-elevated px-2 py-1.5 font-mono text-xs">
            {treasury || "—"}
          </code>
          <button className="btn-ghost btn-sm flex-none" onClick={copy} disabled={!treasury}>
            {copied ? "Copied" : "Copy"}
          </button>
        </div>

        <a className="btn-ghost mb-3 w-full" href={WEB_WALLET} target="_blank" rel="noreferrer">
          Open web wallet to send
          <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        </a>

        <label className="mb-1 block text-xs font-medium text-muted">Transaction ID</label>
        <input
          className="input font-mono"
          placeholder="0x… or the tx hash"
          value={txid}
          onChange={(e) => setTxid(e.target.value)}
        />
        <button
          className="btn-primary mt-3 w-full"
          disabled={depositing || !txid.trim()}
          onClick={() => void submitDeposit(txid)}
        >
          {depositing ? "Verifying…" : "Confirm deposit"}
        </button>
        {error && <p className="mt-2 text-sm text-danger">{error}</p>}
      </div>
    </div>
  );
}
