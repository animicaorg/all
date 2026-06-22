import { useState } from "react";
import { useEnaStore } from "@/state/ena";
import { useWalletStore } from "@/state/wallet";
import { WalletButton } from "@/components/wallet/WalletButton";
import { ManualFund } from "@/components/ena/ManualFund";

const PRESETS = [1, 5, 20];

function fmt(n: number, max = 4): string {
  if (!Number.isFinite(n)) return "0";
  // Trim trailing zeros, keep up to `max` decimals.
  return Number(n.toFixed(max)).toString();
}

// Compact, mobile-first budget control for the ENA "pay with your wallet,
// capped" flow. Shows:
//  - a balance pill (prepaid ANM budget held by the broker)
//  - a cap input + presets (persisted via the ena store)
//  - remaining budget while a run is in flight
//  - a "use my own key" escape hatch
export function BudgetBar({
  sending,
  spentAnm,
  runCap,
  onUseOwnKey,
}: {
  sending: boolean;
  spentAnm: number;
  runCap: number | null;
  onUseOwnKey: () => void;
}) {
  const { balanceAnm, cap, setCap, anmPerKtok, depositing, error, manualOpen, setManualOpen, ensureBudget } =
    useEnaStore();
  const walletConnecting = useWalletStore((s) => s.connecting);
  const walletKind = useWalletStore((s) => s.kind);
  const [draft, setDraft] = useState<string>("");

  function fund() {
    // Injected (one signature) and web (sign-and-send popup) both auto-fund via
    // ensureBudget; with no wallet connected, fall back to manual funding.
    if (walletKind === "injected" || walletKind === "web") void ensureBudget(cap);
    else setManualOpen(true);
  }

  const editing = draft !== "";
  const shownCap = editing ? draft : fmt(cap);

  function commit(v: string) {
    const n = Number(v);
    if (Number.isFinite(n) && n > 0) setCap(n);
    setDraft("");
  }

  const remaining =
    sending && runCap != null ? Math.max(0, runCap - spentAnm) : null;

  return (
    <>
    <div className="flex flex-none flex-col gap-2 border-b border-border bg-elevated/40 px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="chip" title="Prepaid ENA budget held for your runs">
            <svg viewBox="0 0 24 24" className="h-3 w-3" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="9" />
              <path d="M9 12h6M12 9v6" />
            </svg>
            {fmt(balanceAnm)} ANM
          </span>
          {remaining != null && (
            <span className="chip text-ok" title="Remaining budget for this run">
              {fmt(remaining)} left
            </span>
          )}
          {(depositing || walletConnecting) && (
            <span className="text-xs text-muted">
              {walletConnecting ? "Connecting wallet…" : "Confirming deposit…"}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <WalletButton />
          <button className="text-xs text-accent hover:underline" onClick={onUseOwnKey}>
            Use my own key
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <label className="text-xs text-muted">Cap</label>
        <div className="flex items-center gap-1">
          <input
            className="input btn-sm w-20 text-right font-mono"
            inputMode="decimal"
            value={shownCap}
            disabled={sending}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={(e) => commit(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
            }}
          />
          <span className="text-xs text-muted">ANM</span>
        </div>
        <div className="flex items-center gap-1">
          {PRESETS.map((p) => (
            <button
              key={p}
              className={`btn-sm ${cap === p ? "btn-primary" : "btn-ghost"}`}
              disabled={sending}
              onClick={() => setCap(p)}
            >
              {p}
            </button>
          ))}
        </div>
        <button
          className="btn-sm btn-primary ml-auto"
          disabled={sending || depositing}
          onClick={fund}
          title="Top up your prepaid ENA budget"
        >
          {depositing ? "Funding…" : "Fund"}
        </button>
      </div>

      {anmPerKtok > 0 && (
        <p className="text-[11px] leading-snug text-muted">
          Billed by usage — ~{fmt(anmPerKtok, 6)} ANM / 1k tokens (the actual cost of
          each call). One wallet signature funds your budget when it runs short; the
          agent stops at your cap.
        </p>
      )}
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
    {manualOpen && <ManualFund />}
    </>
  );
}
