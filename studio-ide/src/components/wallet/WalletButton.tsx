import { useEffect } from "react";
import { useWalletStore } from "@/state/wallet";

function short(a?: string): string {
  return a ? `${a.slice(0, 6)}…${a.slice(-4)}` : "";
}
function fmtAnm(n: number): string {
  if (!Number.isFinite(n)) return "0";
  return Number(n.toFixed(4)).toString();
}

function WalletIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 flex-none" fill="none" stroke="currentColor" strokeWidth="1.8">
      <path d="M3 7.5A2.5 2.5 0 0 1 5.5 5H18a1 1 0 0 1 1 1v2" />
      <path d="M3 7.5V17a2 2 0 0 0 2 2h14a1 1 0 0 0 1-1v-3" />
      <path d="M21 11.5h-4a2 2 0 0 0 0 4h4a.5.5 0 0 0 .5-.5v-3a.5.5 0 0 0-.5-.5Z" />
    </svg>
  );
}

// Connect-wallet control for the ENA "pay with your wallet" flow.
//  - not connected → a "Connect wallet" button (prompts the window.animica provider)
//  - connected     → a chip with the address + on-chain ANM balance (tap to refresh)
// `compact` hides the label on small screens (header use).
export function WalletButton({ compact = false }: { compact?: boolean }) {
  const { available, connected, connecting, address, balanceAnm, connect, refreshBalance, detect } =
    useWalletStore();

  useEffect(() => {
    detect();
  }, [detect]);

  if (connected) {
    return (
      <button
        className="chip"
        title={`${address}\n${fmtAnm(balanceAnm)} ANM — tap to refresh`}
        onClick={() => void refreshBalance()}
      >
        <span className="h-1.5 w-1.5 flex-none rounded-full bg-ok" />
        <WalletIcon />
        <span className="font-mono">{short(address)}</span>
        <span className="hidden text-muted sm:inline">· {fmtAnm(balanceAnm)} ANM</span>
      </button>
    );
  }

  return (
    <button
      className="btn-ghost btn-sm whitespace-nowrap"
      disabled={connecting}
      onClick={() => void connect()}
      title={available ? "Connect your Animica wallet" : "Animica wallet not detected — install the extension or open in the wallet app"}
    >
      <WalletIcon />
      <span className={compact ? "hidden sm:inline" : ""}>
        {connecting ? "Connecting…" : "Connect wallet"}
      </span>
    </button>
  );
}
